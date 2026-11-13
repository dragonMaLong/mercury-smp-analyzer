"""
smp_parser.py — Micromeritics AutoPore SMP 文件完整解析器
==========================================================

基于对 SMP 二进制格式的完整逆向工程分析。
所有参数从文件自动读取，无需手动输入。
输出原始测量值，不做任何修正或截断。

关键规律（6个文件验证）：
  样品质量偏移 = SUBSET626 size - 5002
  组件质量偏移 = SUBSET626 size - 160
  汞密度记录格式：flag(1B) + temperature(8B) + density(8B)，每条17字节
"""

import struct, math, csv, os, datetime, re, ntpath
from dataclasses import dataclass, field
from typing import List, Dict, Optional


# ══════════════════════════════════════════════════════════════════════
# 数据结构
# ══════════════════════════════════════════════════════════════════════

@dataclass
class MeasurementPoint:
    """单个压力点的原始测量值"""
    pressure_psia:   float
    capacitance_pF:  Optional[float]
    timestamp:       Optional[float]
    mercury_density: Optional[float] = None  # None=无实测记录
    intrusion_volume_mL: Optional[float] = None

@dataclass
class AnalysisResult:
    """单个压力点的计算结果（原始值，不做修正）"""
    pressure_psia:          float
    pore_diameter_nm:       float
    cumulative_volume_mLg:  float
    incremental_volume_mLg: float  # 退汞段为负值，原样保留
    mercury_density_gmL:    float
    capacitance_pF:         Optional[float]

@dataclass
class PenetrometerInfo:
    """穿透计参数（SUBSET628）"""
    model:              str   = ""
    constant_uL_per_pF: float = 0.0
    mass_g:             float = 0.0
    bulb_volume_mL:     float = 0.0
    stem_volume_mL:     float = 0.0
    max_head_psia:      float = 0.0

@dataclass
class MaterialInfo:
    """材料属性（SUBSET634）"""
    name:                      str   = ""
    bet_surface_area_m2g:      float = 0.0
    bulk_density_gmL:          float = 0.0
    true_density_gmL:          float = 0.0
    particle_density_gmL:      float = 0.0
    conductivity_factor:       float = 0.0
    threshold_pressure_psia:   float = 0.0
    linear_compressibility:    float = 0.0
    quadratic_compressibility: float = 0.0

@dataclass
class PressureStep:
    """压力程序中的单个区间"""
    end_pressure_psia:  float
    pressure_increment: float
    equilibration_time: float
    max_intrusion:      float
    scan_rate:          float
    points_per_decade:  int = 0

@dataclass
class BlankCurve:
    """Blank correction curve: embedded in SUBSET628 or built from a blank file.

    ``points`` are (psia, value) in recorded order; the first ``intrusion_count``
    points are the intrusion branch, the rest the extrusion branch. ``value`` is
    absolute mL (legacy embedded curve, divided by the sample mass when applied)
    or, when ``per_gram`` is set, mL per gram of the blank file's own mass.
    """
    reference_path: str
    points: List[tuple]
    intrusion_count: int
    per_gram: bool = False

    @property
    def label(self) -> str:
        return ntpath.basename(self.reference_path) or "内嵌空白"


LegacyBlankCurve = BlankCurve


@dataclass
class SMPFile:
    """SMP 文件完整解析结果"""
    file_path:        str = ""
    version:          str = ""
    created:          str = ""
    modified:         str = ""

    penetrometer:     PenetrometerInfo = field(default_factory=PenetrometerInfo)
    instrument_name:  str   = ""
    instrument_model: str   = ""
    instrument_serial: str  = ""
    instrument_ports: str   = ""
    # Legacy files: LP start, LP end, HP start, HP end (acquisition PC local time).
    analysis_times: Dict[str, str] = field(default_factory=dict)
    analysis_software: str  = ""
    analysis_software_version: str = ""
    software_version: str   = ""

    sample_mass_g:              float = 0.0
    assembly_mass_g:            float = 0.0
    sample_name:                str = ""
    operator:                   str = ""
    submitter:                  str = ""
    bar_code:                   str = ""
    adv_contact_angle_deg:      float = 130.0
    rec_contact_angle_deg:      float = 130.0
    surface_tension_dynes_cm:   float = 485.0
    mercury_temperature_C:      float = 0.0
    mercury_density_gmL:        float = 0.0

    material: MaterialInfo = field(default_factory=MaterialInfo)

    raw_points:       List[MeasurementPoint] = field(default_factory=list)
    lp_buffer_points: List[MeasurementPoint] = field(default_factory=list)
    pressure_program: List[PressureStep]     = field(default_factory=list)
    event_log:        List[str]              = field(default_factory=list)
    measurement_basis: str = "capacitance_pF"
    intrusion_point_count: Optional[int] = None
    warnings: List[str] = field(default_factory=list)
    # Legacy SUBSET628 +236: 1 = None, 2 = Blank (both checked in MicroActive 7.00).
    correction_type_code: Optional[int] = None
    blank_curve: Optional[BlankCurve] = None
    # Runtime choice used by the calculation: "file" (the file's own setting),
    # None (no correction) or a BlankCurve built from a user-selected blank file.
    blank_selection: object = "file"

    _density_map:             Dict[float, float] = field(default_factory=dict)
    _raw_true_density_stored: float = 0.0

    @property
    def recovered_true_density_gmL(self) -> float:
        """修正 MicroActive bug 后的真实真密度（= 存储值 - 样品质量）"""
        if self.measurement_basis == "intrusion_volume_mL":
            # The legacy layout does not establish this MicroActive field.
            return math.nan
        v = self._raw_true_density_stored - self.sample_mass_g
        return v if v > 0.01 else 1.0

    @property
    def mercury_mass_g(self) -> float:
        return self.assembly_mass_g - self.penetrometer.mass_g - self.sample_mass_g


# ══════════════════════════════════════════════════════════════════════
# 解析器
# ══════════════════════════════════════════════════════════════════════

class SMPParser:

    MAGIC = b'MIC##&&FS'

    def parse(self, filepath: str) -> SMPFile:
        with open(filepath, "rb") as f:
            data = f.read()

        if len(data) < 32:
            raise ValueError("SMP 文件头不完整（至少需要 32 字节）")
        if data[2:11] != self.MAGIC:
            raise ValueError("不是有效的 SMP 文件")

        smp = SMPFile()
        smp.file_path = os.path.abspath(filepath)
        self._parse_header(data, smp)

        blocks = self._parse_directory(data)

        if self._is_legacy_layout(data, blocks):
            self._parse_legacy(data, blocks, smp)
            return smp

        if 0x0272 in blocks: self._parse_subset626(data, blocks[0x0272], smp)
        if 0x0273 in blocks: self._parse_subset627(data, blocks[0x0273], smp)
        if 0x0274 in blocks:
            self._parse_subset628(data, blocks[0x0274], smp)
            self._parse_modern_blank(data, blocks[0x0274], smp)
        if 0x0276 in blocks: self._parse_subset630(data, blocks[0x0276], smp)
        if 0x027A in blocks: self._parse_subset634(data, blocks[0x027A], smp)
        if 0x02C1 in blocks: self._parse_subset705(data, blocks[0x02C1], smp)
        if 0x0064 in blocks: self._parse_subset100(data, blocks[0x0064], smp)

        self._parse_lp_buffer(data, blocks.get(0x0276, (30423, 2523))[0], smp)
        _parse_mercury_temperature(data, blocks, smp)
        _resolve_mercury_density(smp)

        return smp

    def calculate(self, smp: SMPFile,
                  contact_angle_deg: float = None,
                  surface_tension_dynes_cm: float = None) -> List[AnalysisResult]:
        """
        按文件中记录的单位计算孔体积和孔径。
        输出原始值：退汞段增量为负值，原样保留，不做截断。
        """
        angle   = contact_angle_deg or smp.adv_contact_angle_deg
        tension = surface_tension_dynes_cm or smp.surface_tension_dynes_cm
        k       = smp.penetrometer.constant_uL_per_pF
        mass    = smp.sample_mass_g

        legacy = smp.measurement_basis == "intrusion_volume_mL"
        if smp.measurement_basis not in {"intrusion_volume_mL", "capacitance_pF"}:
            raise ValueError(f"不支持的测量单位: {smp.measurement_basis}")
        if not math.isfinite(mass) or mass <= 0:
            raise ValueError("样品质量必须为有限正数")
        if not legacy and (not math.isfinite(k) or k <= 0):
            raise ValueError("穿透计常数必须为有限正数")

        results, prev_volume = [], None
        for pt in smp.raw_points:
            raw_value = pt.intrusion_volume_mL if legacy else pt.capacitance_pF
            if raw_value is None or not math.isfinite(raw_value):
                raise ValueError("测量点缺少有效的体积或电容记录")
            vol_mLg = raw_value / mass if legacy else raw_value * k / 1000.0 / mass
            incr_mLg = vol_mLg - prev_volume if prev_volume is not None else 0.0
            results.append(AnalysisResult(
                pressure_psia          = pt.pressure_psia,
                pore_diameter_nm       = (_washburn(pt.pressure_psia, angle, tension)
                                          if pt.pressure_psia > 0 else math.nan),
                cumulative_volume_mLg  = vol_mLg,
                incremental_volume_mLg = incr_mLg,
                mercury_density_gmL    = pt.mercury_density,
                capacitance_pF         = pt.capacitance_pF,
            ))
            prev_volume = vol_mLg
        return results

    # ── 私有解析方法 ──────────────────────────────────────────────

    def _parse_header(self, data, smp):
        if len(data) < 32:
            raise ValueError("SMP 文件头不完整（至少需要 32 字节）")
        smp.version  = data[12:16].decode("ascii", errors="ignore").strip("\x00")
        fmt = "%Y-%m-%d %H:%M:%S"
        smp.created  = datetime.datetime.fromtimestamp(
            struct.unpack_from("<I", data, 16)[0]).strftime(fmt)
        smp.modified = datetime.datetime.fromtimestamp(
            struct.unpack_from("<I", data, 20)[0]).strftime(fmt)

    def _parse_directory(self, data):
        pos = data.find(b'SUBSET101')
        if pos < 0 or pos + 21 > len(data):
            raise ValueError("SMP 目录 SUBSET101 缺失或不完整")
        directory_size = struct.unpack_from("<I", data, pos + 14)[0]
        self._validate_block(data, 101, pos, directory_size)
        n = struct.unpack_from("<H", data, pos + 19)[0]
        if n == 0 or 3 + n * 10 > directory_size:
            raise ValueError("SMP 目录记录数与目录长度不符")
        blocks = {}
        intervals = [(pos, pos + 18 + directory_size, 101)]
        for i in range(n):
            base    = pos + 21 + i * 10
            type_id = struct.unpack_from("<H", data, base)[0]
            offset  = struct.unpack_from("<I", data, base + 2)[0]
            size    = struct.unpack_from("<I", data, base + 6)[0]
            if type_id in blocks or type_id == 101:
                raise ValueError(f"SMP 目录包含重复的 SUBSET{type_id}")
            self._validate_block(data, type_id, offset, size)
            blocks[type_id] = (offset, size)
            intervals.append((offset, offset + 18 + size, type_id))
        intervals.sort()
        for previous, current in zip(intervals, intervals[1:]):
            if current[0] < previous[1]:
                raise ValueError(f"SMP 数据块重叠: SUBSET{previous[2]} / SUBSET{current[2]}")
        return blocks

    @staticmethod
    def _validate_block(data, type_id, offset, size):
        # Directory sizes describe the payload; the block header adds 18 bytes.
        if offset < 32 or size < 1 or offset + 18 + size > len(data):
            raise ValueError(f"SMP 数据块 SUBSET{type_id} 越界或被截断")
        expected_tag = f"SUBSET{type_id:03d}\0".encode("ascii")
        header_id, header_size = struct.unpack_from("<HI", data, offset + 12)
        if (data[offset:offset + 10] != expected_tag
                or header_id != type_id or header_size != size):
            raise ValueError(f"SMP 目录与 SUBSET{type_id} 块头不一致")

    @staticmethod
    def _is_legacy_layout(data, blocks):
        # Both signatures are required. A partial match must not fall through
        # to the modern scanner and manufacture capacitance records.
        sample = blocks.get(626)
        measurements = blocks.get(630)
        old_sample = sample is not None and (sample[1] == 924 or data[sample[0] + 18] == 7)
        old_measurements = measurements is not None and data[measurements[0] + 18] == 2
        if not (old_sample or old_measurements):
            return False
        expected = {626: (7, 924), 627: (1, None), 628: (2, None), 630: (2, None)}
        for type_id, (revision, size) in expected.items():
            block = blocks.get(type_id)
            if block is None:
                raise ValueError(f"旧版 SMP 缺少必要数据块 SUBSET{type_id}")
            if data[block[0] + 18] != revision or (size is not None and block[1] != size):
                raise ValueError(f"不支持的旧版 SMP 布局: SUBSET{type_id}")
        return True

    def _parse_legacy(self, data, blocks, smp):
        """Verified ASCII rev-7 sample / rev-2 16-byte volume-record layout.

        Offsets below include each 18-byte SUBSET header. The second double
        is absolute mL, independently checked against an original IV report.
        It must never be treated as capacitance. Original blank corrections
        and report options are not reconstructed from this layout.
        """
        def read(type_id, rel, fmt):
            offset, size = blocks[type_id]
            if rel < 18 or rel + struct.calcsize(fmt) > size + 18:
                raise ValueError(f"旧版 SMP 的 SUBSET{type_id} 参数被截断")
            return struct.unpack_from(fmt, data, offset + rel)

        def text_field(type_id, start, end):
            offset, size = blocks[type_id]
            if not (18 <= start < end <= size + 18):
                raise ValueError(f"旧版 SMP 的 SUBSET{type_id} 文本字段被截断")
            value = data[offset + start:offset + end].split(b"\0", 1)[0]
            return value.decode("ascii", errors="replace").strip()

        def number(type_id, rel, label, *, allow_zero=False):
            value = read(type_id, rel, "<d")[0]
            if not math.isfinite(value) or value < 0 or (value == 0 and not allow_zero):
                raise ValueError(f"旧版 SMP 的{label}必须为有限{'非负数' if allow_zero else '正数'}")
            return value

        smp.measurement_basis = "intrusion_volume_mL"
        smp.sample_mass_g = number(626, 19, "样品质量")
        smp.assembly_mass_g = number(626, 751, "组件质量")
        smp.sample_name = text_field(626, 29, 80)
        smp.operator = text_field(626, 80, 101)
        smp.material.name = text_field(626, 875, 896)
        smp.material.true_density_gmL = math.nan
        smp.mercury_temperature_C = math.nan

        smp.penetrometer.model = text_field(628, 19, 115)
        for rel, attr, label in (
            (115, "constant_uL_per_pF", "穿透计常数"),
            (123, "mass_g", "穿透计质量"),
            (131, "bulb_volume_mL", "球泡体积"),
            (139, "stem_volume_mL", "茎管体积"),
            (147, "max_head_psia", "最大头压"),
        ):
            setattr(smp.penetrometer, attr, number(628, rel, label, allow_zero=True))
        for rel, attr, label in (
            (298, "adv_contact_angle_deg", "前进接触角"),
            (306, "rec_contact_angle_deg", "后退接触角"),
            (314, "surface_tension_dynes_cm", "表面张力"),
            (322, "mercury_density_gmL", "汞密度"),
        ):
            value = number(627, rel, label)
            if "angle" in attr and value > 180:
                raise ValueError(f"旧版 SMP 的{label}超出 180 度")
            setattr(smp, attr, value)

        count, intrusion_count = read(630, 229, "<IH")
        if count == 0 or blocks[630][1] != 217 + 16 * count:
            raise ValueError("旧版 SMP 测量点数与 SUBSET630 长度不符")
        if not 1 <= intrusion_count <= count:
            raise ValueError("旧版 SMP 的进汞点数超出总测量点数")
        smp.intrusion_point_count = intrusion_count
        # SUBSET630: LP / HP instrument serial (ASCII, +84 / +116) and ports
        # (uint16, +148 / +150); SUBSET100 carries the instrument family tag.
        # Checked against vendor report headers ("Serial # 830  Port 1/1",
        # "Serial # 1404  Port 2/2").
        smp.instrument_serial = text_field(630, 84, 116) or text_field(630, 116, 148)
        lp_port, hp_port = read(630, 148, "<HH")
        if 0 < lp_port < 100 and 0 < hp_port < 100:
            smp.instrument_ports = f"{lp_port}/{hp_port}"
        # Four C "struct tm" records (9 x uint16) in acquisition-PC local time.
        # LP end / HP end equal the vendor reports' LP / HP Analysis Time. The
        # file header timestamp is not used for legacy files: the IV 9500
        # software encodes local time as if it were US Eastern time.
        for name, rel in (("lp_start", 154), ("lp_end", 172), ("hp_start", 190), ("hp_end", 208)):
            sec, minute, hour, day, month, year, *_ = read(630, rel, "<9H")
            try:
                stamp = datetime.datetime(1900 + year, month + 1, day, hour, minute, sec)
            except ValueError:
                continue
            if year >= 80:
                smp.analysis_times[name] = stamp.strftime("%Y-%m-%d %H:%M:%S")
        times = smp.analysis_times
        if "lp_start" in times or "hp_start" in times:
            smp.created = times.get("lp_start") or times["hp_start"]
            smp.modified = times.get("hp_end") or times.get("lp_end") or smp.created
        if 100 in blocks:
            offset, size = blocks[100]
            tag = re.search(rb"(?<![0-9A-Za-z])9[0-9]{1,2}[0-9X]{2}(?![0-9A-Za-z])",
                            data[offset + 18:offset + 18 + size])
            if tag:
                smp.instrument_model = tag.group().decode("ascii")
        for index in range(count):
            pressure, volume = read(630, 235 + index * 16, "<dd")
            if not math.isfinite(pressure) or not math.isfinite(volume):
                raise ValueError(f"旧版 SMP 第 {index + 1} 个测量点包含非有限数值")
            smp.raw_points.append(MeasurementPoint(
                pressure_psia=pressure, capacitance_pF=None, timestamp=None,
                mercury_density=smp.mercury_density_gmL, intrusion_volume_mL=volume,
            ))
        self._parse_legacy_blank(data, blocks[628], smp)
        smp.warnings.append("旧版 SMP 保存压力与未经空白校正的绝对进汞体积（mL），不应用电容常数或额外头压修正。")
        if smp.blank_curve is not None:
            smp.warnings.append(
                f"文件含空白校正设置及内嵌空白曲线（{smp.blank_curve.reference_path or '未记录路径'}，"
                f"{len(smp.blank_curve.points)} 点）；原始记录保持未校正。")
        elif smp.correction_type_code not in (None, 1):
            smp.warnings.append(
                f"文件记录的校正类型代码为 {smp.correction_type_code}，尚未验证其含义，未应用任何校正。")
        smp.warnings.append("旧版文件只记录仪器系列代号和序列号，软件版本及真密度未知。")
        nonpositive_pressure = sum(point.pressure_psia <= 0 for point in smp.raw_points)
        if nonpositive_pressure:
            smp.warnings.append(f"原始数据含 {nonpositive_pressure} 个非正压力点；保留原始记录，这些点不能计算孔径。")
        negative_volume = sum(point.intrusion_volume_mL < 0 for point in smp.raw_points)
        if negative_volume:
            smp.warnings.append(f"原始数据含 {negative_volume} 个负进汞体积点，已保留符号，需核对原始实验和校正设置。")

    @staticmethod
    def _parse_legacy_blank(data, block, smp):
        """SUBSET628 rev 2 tail (offsets include the 18-byte header).

        +155..+236 NUL-terminated blank file path on the acquisition PC
        +236 uint8 correction type (1 = None, 2 = Blank; others unverified)
        +238 uint16 blank point count N, +242 uint16 blank intrusion count
        +244 N x <dd> (psia, mL), ending exactly at the block end.
        """
        offset, size = block
        end = offset + 18 + size
        if end < offset + 237:
            return
        smp.correction_type_code = data[offset + 236]
        if smp.correction_type_code != 2:
            return
        if end < offset + 244:
            raise ValueError("旧版 SMP 声明了空白校正，但 SUBSET628 缺少空白曲线")
        count, intrusion_count = struct.unpack_from("<H2xH", data, offset + 238)
        if count < 2 or offset + 244 + 16 * count != end or not 1 <= intrusion_count <= count:
            raise ValueError("旧版 SMP 的空白曲线长度与 SUBSET628 不符")
        points = [struct.unpack_from("<dd", data, offset + 244 + 16 * i) for i in range(count)]
        if not all(math.isfinite(p) and math.isfinite(v) for p, v in points):
            raise ValueError("旧版 SMP 的空白曲线包含非有限数值")
        path = data[offset + 155:offset + 236].split(b"\0", 1)[0]
        smp.blank_curve = BlankCurve(
            reference_path=path.decode("ascii", errors="replace").strip(),
            points=points,
            intrusion_count=intrusion_count,
        )

    @staticmethod
    def _parse_modern_blank(data, block, smp):
        """SUBSET628 rev 3 tail, located from the block end (path is variable length).

        No blank: ... [rev u8][code u16 = 1][N u32 = 0]
        Blank:    UTF-16 path NUL, [rev u8][code u16 = 2][N u32][M u16],
                  then N x <ddd> (psia, x, 0) to the block end, where
                  -x * penetrometer constant / 1000 is the blank volume in mL per
                  gram of the blank file's mass. Layout taken from a file saved
                  by MicroActive 7.00 after "Select Blank"; intrusion results
                  were checked against its tabular report.
        """
        offset, size = block
        full = data[offset:offset + 18 + size]
        length = len(full)
        if length < 26:
            return
        revision = full[18]
        if full[length - 7] == revision and full[length - 4:] == b"\0\0\0\0":
            smp.correction_type_code = struct.unpack_from("<H", full, length - 6)[0]
            return
        for count in range(1, (length - 28) // 24 + 1):
            pos = length - 24 * count - 9
            if pos < 21:
                break
            if full[pos] != revision or full[pos - 2:pos] != b"\0\0":
                continue
            code, declared, split = struct.unpack_from("<HIH", full, pos + 1)
            if declared != count or not 1 <= split <= count:
                continue
            smp.correction_type_code = code
            if code != 2:
                return
            k = smp.penetrometer.constant_uL_per_pF
            records = [struct.unpack_from("<ddd", full, pos + 9 + 24 * i) for i in range(count)]
            if not (math.isfinite(k) and k > 0) or not all(
                    math.isfinite(p) and math.isfinite(x) for p, x, _ in records):
                raise ValueError("SMP 的内嵌空白曲线或膨胀计常数无效")
            start = pos - 2
            while start >= 20 and full[start - 1] == 0 and 32 <= full[start - 2] < 127:
                start -= 2
            smp.blank_curve = BlankCurve(
                reference_path=full[start:pos - 2].decode("utf-16le", errors="replace").strip(),
                points=[(p, -x * k / 1000.0) for p, x, _ in records],
                intrusion_count=split,
                per_gram=True,
            )
            smp.warnings.append(
                f"文件含空白校正设置及内嵌空白曲线（{smp.blank_curve.label}，{count} 点）。")
            return

    def _parse_subset626(self, data, block, smp):
        """
        SUBSET626 样品描述

        关键规律（6个文件验证）：
          样品质量偏移 = size - 5002
          组件质量偏移 = size - 160
          Bug值偏移    = 样品质量偏移 + 16（即 +201 对应 size=5187 时）

        原因：SUBSET626 包含变长 UTF-16 字段（样品名、操作员等），
        这些字段有内容时会占用更多字节，导致后续数值字段整体后移。
        通过 size 反推偏移可以消除这种影响。
        """
        o, size = block
        chunk = data[o : o + size]
        _parse_subset626_text_fields(chunk, smp)

        # 样品质量：偏移 = size - 5002
        mass_rel = size - 5002
        if mass_rel > 0:
            v = struct.unpack_from("<d", data, o + mass_rel)[0]
            if 0 < v < 1000:
                smp.sample_mass_g = v

        # Bug值（真密度+样品质量）：偏移 = size - 4986
        bug_rel = size - 4986
        if bug_rel > 0:
            smp._raw_true_density_stored = struct.unpack_from("<d", data, o + bug_rel)[0]

        # 组件质量：偏移 = size - 160
        assem_rel = size - 160
        if assem_rel > 0:
            v = struct.unpack_from("<d", data, o + assem_rel)[0]
            if 0 < v < 10000:
                smp.assembly_mass_g = v

    def _parse_subset627(self, data, block, smp):
        """
        SUBSET627 方法参数

        固定相对偏移（不受 size 影响，已在6个文件验证）：
          +59  : LP平衡时间（uint8，s）
          +76  : HP平衡时间（uint8，s）
          +91  : HP分析模式（uint8，1或2）
          +138 : 前进接触角（double，°）
          +146 : 后退接触角（double，°）
          +154 : 表面张力（double，dynes/cm）
          ~+188: 汞密度记录起点（逐字节扫描）
          +908 : 压力程序表起点

        汞密度记录格式（17字节/条）：
          [flag uint8(1B)] [temperature double(8B)] [density double(8B)]
        """
        o, size = block

        # 接触角、表面张力
        for rel, attr, lo, hi in [
            (138, 'adv_contact_angle_deg',     90,  180),
            (146, 'rec_contact_angle_deg',      90,  180),
            (154, 'surface_tension_dynes_cm',  100,  600),
        ]:
            v = struct.unpack_from("<d", data, o + rel)[0]
            if lo < v < hi:
                setattr(smp, attr, v)

        # Records are flag + temperature + density (17 bytes). Pairing a
        # density with the following temperature shifts the entire table.
        density_map = {}
        payload = data[o:o+size]
        best_run = []
        for start in range(0, max(0, len(payload) - 16)):
            run = []
            i = start
            while i + 17 <= len(payload):
                flag = payload[i]
                temp, d = struct.unpack_from("<dd", payload, i + 1)
                if flag == 171:
                    break
                if not (flag == 1 and 13.0 < d < 14.0 and 0.0 < temp < 100.0):
                    break
                run.append((round(temp, 2), d))
                i += 17
            if len(run) > len(best_run):
                best_run = run
        best_run = _longest_monotonic_density_run(best_run)
        for temp, density in best_run:
            density_map[temp] = density
        smp._density_map = density_map

        # 压力程序表
        prog_start = o + 908
        smp.pressure_program = []
        for _ in range(200):
            if prog_start + 43 > o + size: break
            ep  = struct.unpack_from("<d", data, prog_start)[0]
            inc = struct.unpack_from("<d", data, prog_start + 8)[0]
            eqt = struct.unpack_from("<d", data, prog_start + 16)[0]
            mxi = struct.unpack_from("<d", data, prog_start + 24)[0]
            sr  = struct.unpack_from("<d", data, prog_start + 32)[0]
            ppd = struct.unpack_from("<H", data, prog_start + 41)[0]
            if not (0.01 < ep < 100000): break
            smp.pressure_program.append(PressureStep(ep, inc, eqt, mxi, sr, ppd))
            prog_start += 43

    def _parse_subset628(self, data, block, smp):
        o, size = block
        chunk = data[o : o + size]
        strings = _extract_utf16(chunk, min_len=3)
        if strings:
            smp.penetrometer.model = strings[0]
        # The numeric payload can shift by a few bytes between files.  Search
        # for the coherent five-double penetrometer record instead of assuming
        # one fixed offset.
        def rd(rel):
            if rel < 0 or rel >= size or o + rel + 8 > len(data):
                return None
            v = struct.unpack_from("<d", data, o + rel)[0]
            return v if math.isfinite(v) else None

        record = None
        for base in range(120, max(121, size - 31)):
            values = [rd(base + step) for step in (0, 8, 16, 24, 32)]
            if any(v is None for v in values):
                continue
            constant, mass, bulb, stem, max_head = values
            if (
                1.0 <= constant <= 100.0
                and 20.0 <= mass <= 200.0
                and 0.5 <= bulb <= 50.0
                and 0.05 <= stem <= 2.0
                and 0.5 <= max_head <= 20.0
            ):
                record = values
                break

        if record is None:
            record = [rd(rel) for rel in (149, 157, 165, 173, 181)]

        for attr, value in zip(
            (
                "constant_uL_per_pF",
                "mass_g",
                "bulb_volume_mL",
                "stem_volume_mL",
                "max_head_psia",
            ),
            record,
        ):
            if value is not None:
                setattr(smp.penetrometer, attr, value)

    def _parse_subset630(self, data, block, smp):
        """
        核心测量数据：每条24字节 [压力(8B)][电容(8B)][时间戳(8B)]
        数据起点非固定，需逐字节扫描（通常在块头部后317~321字节处）。
        """
        o, size = block
        _parse_instrument_software_text(data[o : o + size], smp)

        end = o + size
        hard_end = len(data)

        def is_record(abs_offset, limit):
            if abs_offset + 24 > limit:
                return False
            p = struct.unpack_from("<d", data, abs_offset)[0]
            cap = struct.unpack_from("<d", data, abs_offset + 8)[0]
            ts = struct.unpack_from("<d", data, abs_offset + 16)[0]
            return (
                math.isfinite(p)
                and math.isfinite(cap)
                and math.isfinite(ts)
                and 0.1 < p < 60000
                and abs(cap) < 1e6
                and abs(ts) < 1e12
            )

        start, best_count = None, 0
        for candidate in range(o, end - 23):
            if not is_record(candidate, end):
                continue
            count = 0
            i = candidate
            while is_record(i, end):
                count += 1
                i += 24
            if count > best_count:
                start, best_count = candidate, count
        if start is None: return

        total_count = best_count
        next_offset = start + best_count * 24
        if is_record(next_offset, hard_end):
            total_count += 1

        i = start
        for _ in range(total_count):
            p   = struct.unpack_from("<d", data, i)[0]
            cap = struct.unpack_from("<d", data, i + 8)[0]
            ts  = struct.unpack_from("<d", data, i + 16)[0]
            smp.raw_points.append(MeasurementPoint(
                pressure_psia   = p,
                capacitance_pF  = abs(cap),
                timestamp       = ts,
                mercury_density = None,  # 汞密度以温度查表方式存储，暂不关联
            ))
            i += 24

    def _parse_subset634(self, data, block, smp):
        o = block[0]
        chunk = data[o : o + block[1]]
        strings = _extract_utf16(chunk, min_len=3)
        if strings:
            smp.material.name = strings[0]
        m = smp.material
        def rd(rel):
            abs_off = o + rel
            return struct.unpack_from("<d", data, abs_off)[0] if abs_off + 8 <= len(data) else 0.0
        m.bet_surface_area_m2g      = rd(40)
        m.bulk_density_gmL          = rd(50)
        m.true_density_gmL          = rd(58)
        m.particle_density_gmL      = rd(66)
        m.conductivity_factor       = rd(76)
        m.threshold_pressure_psia   = rd(86)
        m.linear_compressibility    = rd(94)
        m.quadratic_compressibility = rd(102)

    def _parse_subset705(self, data, block, smp):
        o, size = block
        smp.event_log = _extract_utf16(data[o : o + size], min_len=5)

    def _parse_subset100(self, data, block, smp):
        o, size = block
        for s in _extract_utf16(data[o : o + size], min_len=2):
            if s.startswith("V") and len(s) < 10:
                smp.software_version = s
            if "9600" in s or "6000" in s:
                smp.instrument_model = s

    def _parse_lp_buffer(self, data, subset630_offset, smp):
        """gap 区域：LP 端口原始缓冲（offset 29246 ~ SUBSET630起点）"""
        GAP_START = 29246
        GAP_END   = subset630_offset

        start = None
        for i in range(GAP_START + 10, GAP_END - 48):
            p1 = struct.unpack_from("<d", data, i)[0]
            if 0.1 < p1 < 60000:
                p2 = struct.unpack_from("<d", data, i + 24)[0]
                if 0.1 < p2 < 60000:
                    start = i
                    break
        if start is None: return

        i = start
        while i + 24 <= GAP_END:
            p   = struct.unpack_from("<d", data, i)[0]
            cap = struct.unpack_from("<d", data, i + 8)[0]
            ts  = struct.unpack_from("<d", data, i + 16)[0]
            if not (0.1 < p < 60000): break
            smp.lp_buffer_points.append(MeasurementPoint(
                pressure_psia   = p,
                capacitance_pF  = abs(cap),
                timestamp       = ts,
                mercury_density = None,
            ))
            i += 24


# ══════════════════════════════════════════════════════════════════════
# 工具函数
# ══════════════════════════════════════════════════════════════════════

def _parse_mercury_temperature(data, blocks, smp):
    subset630 = blocks.get(0x0276)
    if not subset630:
        return

    # Measured temperature is the double in the trailer after SUBSET630.
    # Unlike the old FREE-space scan this also handles compacted SMP files.
    temperature_offset = subset630[0] + subset630[1] + 10
    if temperature_offset + 8 <= len(data):
        value = struct.unpack_from("<d", data, temperature_offset)[0]
        if math.isfinite(value) and 0.0 < value < 100.0:
            smp.mercury_temperature_C = float(value)
            return

    gap_start = 0
    if 0x02C1 in blocks:
        gap_start = blocks[0x02C1][0] + blocks[0x02C1][1]
    gap_end = subset630[0]
    if not (0 <= gap_start < gap_end <= len(data)):
        return

    marker = data.find(b"FREE", gap_start, gap_end)
    candidates = []
    if marker > gap_start:
        for offset in range(max(gap_start, marker - 24), marker):
            if offset + 8 > len(data):
                continue
            value = struct.unpack_from("<d", data, offset)[0]
            if math.isfinite(value) and 10.0 < value < 40.0:
                candidates.append(value)

    if not candidates:
        for offset in range(gap_start, gap_end - 7):
            value = struct.unpack_from("<d", data, offset)[0]
            if math.isfinite(value) and 10.0 < value < 40.0 and abs(value - round(value)) > 0.001:
                candidates.append(value)

    if candidates:
        smp.mercury_temperature_C = float(candidates[0])


def _longest_monotonic_density_run(records):
    best = []
    for start in range(len(records)):
        current = [records[start]]
        for record in records[start + 1:]:
            previous_temp, previous_density = current[-1]
            temp, density = record
            if temp > previous_temp and density <= previous_density:
                current.append(record)
            else:
                break
        if len(current) > len(best):
            best = current
    return best


def _resolve_mercury_density(smp):
    if not smp._density_map:
        return

    temperatures = sorted(smp._density_map)
    densities = [smp._density_map[temp] for temp in temperatures]
    if smp.mercury_temperature_C > 0:
        smp.mercury_density_gmL = float(_linear_interpolate(smp.mercury_temperature_C, temperatures, densities))
        return

    if 23.0 in smp._density_map:
        smp.mercury_density_gmL = smp._density_map[23.0]
    else:
        smp.mercury_density_gmL = densities[len(densities) // 2]


def _linear_interpolate(x, x_values, y_values):
    if not x_values:
        return 0.0
    if x <= x_values[0]:
        return y_values[0]
    if x >= x_values[-1]:
        return y_values[-1]
    for i in range(1, len(x_values)):
        if x <= x_values[i]:
            x0, x1 = x_values[i - 1], x_values[i]
            y0, y1 = y_values[i - 1], y_values[i]
            if x1 == x0:
                return y0
            fraction = (x - x0) / (x1 - x0)
            return y0 + fraction * (y1 - y0)
    return y_values[-1]


def _akima_slopes(xs, ys):
    m = [(ys[i + 1] - ys[i]) / (xs[i + 1] - xs[i]) for i in range(len(xs) - 1)]
    if len(m) == 1:
        return [m[0], m[0]]
    # Akima (1970) end extrapolation of segment slopes.
    mm = [3 * m[0] - 2 * m[1], 2 * m[0] - m[1], *m, 2 * m[-1] - m[-2], 3 * m[-1] - 2 * m[-2]]
    slopes = []
    for i in range(len(xs)):
        m1, m2, m3, m4 = mm[i:i + 4]
        w1, w2 = abs(m4 - m3), abs(m2 - m1)
        slopes.append((m2 + m3) / 2 if w1 + w2 == 0 else (w1 * m2 + w2 * m3) / (w1 + w2))
    return slopes


def blank_curve_value(curve: BlankCurve, pressure_psia: float, extrusion: bool) -> float:
    """Blank value (in the curve's own unit) at a sample pressure, on the matching branch.

    Reproduces MicroActive 7.00 for the 2513-1/2/3 IV files: Akima spline in
    linear pressure, each interval clamped to its endpoint values, constant
    outside the branch. The extrusion branch excludes the blank's
    maximum-pressure point. Checked point by point against the vendor tabular
    report: intrusion within 2e-6 mL/g, extrusion within 1.5e-5 mL/g.
    """
    split = curve.intrusion_count
    branch = curve.points[split:] if extrusion else curve.points[:split]
    if not branch:
        branch = curve.points[split - 1:split]
    xs, ys = [], []
    for p, v in sorted(branch, key=lambda item: item[0]):
        if not xs or p > xs[-1]:
            xs.append(p)
            ys.append(v)
    if pressure_psia <= xs[0]:
        return ys[0]
    if pressure_psia >= xs[-1]:
        return ys[-1]
    t = _akima_slopes(xs, ys)
    j = max(i for i in range(len(xs) - 1) if xs[i] <= pressure_psia)
    h = xs[j + 1] - xs[j]
    s = (pressure_psia - xs[j]) / h
    y = ((2 * s**3 - 3 * s**2 + 1) * ys[j] + (s**3 - 2 * s**2 + s) * h * t[j]
         + (-2 * s**3 + 3 * s**2) * ys[j + 1] + (s**3 - s**2) * h * t[j + 1])
    return min(max(y, min(ys[j], ys[j + 1])), max(ys[j], ys[j + 1]))


legacy_blank_volume_mL = blank_curve_value


def _washburn(pressure_psia, contact_angle_deg, surface_tension_dynes_cm):
    P_pa  = pressure_psia * 6894.757
    theta = math.radians(contact_angle_deg)
    gamma = surface_tension_dynes_cm * 1e-3
    return -4.0 * gamma * math.cos(theta) / P_pa * 1e9


def _extract_utf16(chunk, min_len=3):
    strings, i = [], 0
    while i < len(chunk) - 1:
        if 0x20 <= chunk[i] <= 0x7E and chunk[i + 1] == 0x00:
            s = []
            while i < len(chunk) - 1 and 0x20 <= chunk[i] <= 0x7E and chunk[i + 1] == 0x00:
                s.append(chr(chunk[i]))
                i += 2
            if len(s) >= min_len:
                strings.append(''.join(s))
        else:
            i += 1
    return strings


def _parse_instrument_software_text(chunk, smp):
    for text in _extract_utf16(chunk, min_len=8):
        compact = " ".join(text.split())
        if "MicroActive" not in compact or "AutoPore" not in compact:
            continue

        smp.analysis_software = "MicroActive"

        version_match = re.search(r"Version\s*([0-9][0-9A-Za-z.\-]*)", compact, re.IGNORECASE)
        if version_match:
            smp.analysis_software_version = version_match.group(1)

        instrument_match = re.search(r"(AutoPore\s+V\s*\d+)", compact, re.IGNORECASE)
        if instrument_match:
            instrument = re.sub(r"\s+", " ", instrument_match.group(1)).strip()
            instrument = re.sub(r"V\s+(\d+)", r"V\1", instrument)
            smp.instrument_name = instrument
            model_match = re.search(r"(\d+)", instrument)
            if model_match:
                smp.instrument_model = model_match.group(1)
        return


def _parse_subset626_text_fields(chunk, smp):
    values = _subset626_marker_texts(chunk)
    if not any(values):
        values = _subset626_ascii_texts(chunk)

    fields = (values + ["", "", "", ""])[:4]
    smp.sample_name = _clean_metadata_text(fields[0])
    smp.operator = _clean_metadata_text(fields[1])
    smp.submitter = _clean_metadata_text(fields[2])
    smp.bar_code = _clean_metadata_text(fields[3])


def _subset626_marker_texts(chunk):
    label_pos = _first_label_position(chunk)
    if label_pos < 0:
        label_pos = min(len(chunk), 220)

    values = []
    start = 0
    marker = b"\xe0\x01\x00"
    while len(values) < 4:
        pos = chunk.find(marker, start, label_pos)
        if pos < 0 or pos + 7 > len(chunk):
            break
        length = int.from_bytes(chunk[pos + 3 : pos + 7], "little")
        text = ""
        if 0 <= length <= 512 and pos + 7 + length <= len(chunk):
            payload = chunk[pos + 7 : pos + 7 + length]
            text = payload.decode("utf-16-le", errors="ignore")
        values.append(_clean_metadata_text(text))
        start = pos + 1
    return values


def _subset626_ascii_texts(chunk):
    label_pos = _first_label_position(chunk)
    limit = label_pos if label_pos > 0 else min(len(chunk), 180)
    runs = []
    i = 10
    while i < limit:
        if 0x20 <= chunk[i] <= 0x7E:
            start = i
            chars = []
            while i < limit and 0x20 <= chunk[i] <= 0x7E:
                chars.append(chr(chunk[i]))
                i += 1
            text = _clean_metadata_text("".join(chars))
            if text and text != "SUBSET626":
                runs.append((start, text))
        else:
            i += 1

    sample_candidates = [(start, text) for start, text in runs if start >= 20 and len(text) >= 3]
    sample = max(sample_candidates, key=lambda item: len(item[1]))[1] if sample_candidates else ""
    operator = ""
    submitter = ""
    if sample:
        sample_start = next(start for start, text in runs if text == sample)
        after_sample = [(start, text) for start, text in runs if start > sample_start and text != sample]
        if after_sample:
            operator = after_sample[0][1]
        if len(after_sample) > 1:
            submitter = after_sample[1][1]
    return [sample, operator, submitter, ""]


def _first_label_position(chunk):
    positions = []
    for label in (b"Sample:", "Sample:".encode("utf-16-le")):
        pos = chunk.find(label)
        if pos >= 0:
            positions.append(pos)
    return min(positions) if positions else -1


def _clean_metadata_text(value):
    text = str(value or "").replace("\x00", "").strip()
    if text in {"", "?", "??", "???", " "}:
        return ""
    if text.endswith(":") and text in {"Sample:", "Operator:", "Submitter:", "Bar Code:"}:
        return ""
    return text


# ══════════════════════════════════════════════════════════════════════
# 输出函数
# ══════════════════════════════════════════════════════════════════════

def export_csv(results: List[AnalysisResult],
               output_path: str,
               smp: Optional[SMPFile] = None):
    with open(output_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        if smp:
            w.writerow(["# SMP Parser — 原始数据输出"])
            w.writerow(["# 文件",       smp.file_path])
            w.writerow(["# 创建时间",   smp.created])
            w.writerow(["# 穿透计",     smp.penetrometer.model])
            w.writerow(["# 穿透计常数", f"{smp.penetrometer.constant_uL_per_pF} μL/pF"])
            w.writerow(["# 样品质量",   f"{smp.sample_mass_g} g"])
            w.writerow(["# 接触角",     f"{smp.adv_contact_angle_deg} °"])
            w.writerow(["# 表面张力",   f"{smp.surface_tension_dynes_cm} dynes/cm"])
            if smp.measurement_basis == "intrusion_volume_mL":
                w.writerow(["# 测量单位", "文件内绝对进汞体积（mL），除以样品质量得到 mL/g"])
                w.writerow(["# 注：保留文件内压力及体积，不应用电容常数或额外头压修正"])
                for warning in smp.warnings:
                    w.writerow(["# 提示", warning])
            else:
                w.writerow(["# 注：压力为传感器原始值，未做汞柱头压修正"])
            w.writerow([])
        w.writerow([
            "Pressure (psia)", "Pore Diameter (nm)",
            "Capacitance (pF)", "Cumulative Volume (mL/g)",
            "Incremental Volume (mL/g)", "Mercury Density (g/mL)",
        ])
        for r in results:
            dens = f"{r.mercury_density_gmL:.4f}" if r.mercury_density_gmL is not None else ""
            w.writerow([
                f"{r.pressure_psia:.6f}",
                f"{r.pore_diameter_nm:.3f}",
                f"{r.capacitance_pF:.6f}" if r.capacitance_pF is not None else "",
                f"{r.cumulative_volume_mLg:.9f}",
                f"{r.incremental_volume_mLg:.9f}",
                dens,
            ])
    print(f"已导出: {output_path}（{len(results)} 个数据点）")


def print_summary(smp: SMPFile, results: List[AnalysisResult]):
    W = 62
    print("═" * W)
    print("  SMP 文件解析摘要")
    print("═" * W)
    print(f"  文件        : {os.path.basename(smp.file_path)}")
    print(f"  版本        : {smp.version}")
    print(f"  创建时间    : {smp.created}")
    print(f"  修改时间    : {smp.modified}")
    print("─" * W)
    print(f"  仪器        : AutoPore {smp.instrument_model}  (v{smp.software_version})")
    print(f"  穿透计      : {smp.penetrometer.model}")
    print(f"  穿透计常数  : {smp.penetrometer.constant_uL_per_pF} μL/pF")
    print(f"  球泡体积    : {smp.penetrometer.bulb_volume_mL} mL")
    print(f"  茎管体积    : {smp.penetrometer.stem_volume_mL} mL")
    print(f"  最大头压    : {smp.penetrometer.max_head_psia} psia")
    print("─" * W)
    print(f"  材料        : {smp.material.name}")
    print(f"  BET比表面积 : {smp.material.bet_surface_area_m2g} m²/g")
    print(f"  样品质量    : {smp.sample_mass_g} g")
    print(f"  组件质量    : {smp.assembly_mass_g} g")
    print(f"  汞质量      : {smp.mercury_mass_g:.4f} g")
    print(f"  真密度(修正): {smp.recovered_true_density_gmL:.4f} g/mL")
    print("─" * W)
    print(f"  前进接触角  : {smp.adv_contact_angle_deg} °")
    print(f"  后退接触角  : {smp.rec_contact_angle_deg} °")
    print(f"  表面张力    : {smp.surface_tension_dynes_cm} dynes/cm")
    print(f"  线性压缩系数: {smp.material.linear_compressibility:.2e} 1/psia")
    print(f"  二次压缩系数: {smp.material.quadratic_compressibility:.2e} 1/psia²")
    print("─" * W)
    print(f"  数据点总数  : {len(results)}")
    print(f"  LP缓冲点数  : {len(smp.lp_buffer_points)}")
    print(f"  压力程序段数: {len(smp.pressure_program)}")
    if results:
        total = max(r.cumulative_volume_mLg for r in results)
        pmin  = min(r.pressure_psia for r in results)
        pmax  = max(r.pressure_psia for r in results)
        dmax  = _washburn(pmin, smp.adv_contact_angle_deg, smp.surface_tension_dynes_cm)
        dmin  = _washburn(pmax, smp.adv_contact_angle_deg, smp.surface_tension_dynes_cm)
        print(f"  最大累积孔体积: {total:.4f} mL/g")
        print(f"  孔径范围    : {dmin:.1f} ~ {dmax:.1f} nm")
    print("─" * W)
    if smp.event_log:
        print("  事件日志    :")
        for e in smp.event_log:
            print(f"    · {e}")
    print("═" * W)
