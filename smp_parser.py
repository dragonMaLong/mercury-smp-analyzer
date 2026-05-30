"""
smp_parser.py — Micromeritics AutoPore SMP 文件完整解析器
==========================================================

基于对 SMP 二进制格式的完整逆向工程分析。
所有参数从文件自动读取，无需手动输入。
输出原始测量值，不做任何修正或截断。

关键规律（6个文件验证）：
  样品质量偏移 = SUBSET626 size - 5002
  组件质量偏移 = SUBSET626 size - 160
  汞密度记录格式：density(8B) + flag(1B) + temperature(8B)，每条17字节
"""

import struct, math, csv, os, datetime, re
from dataclasses import dataclass, field
from typing import List, Dict, Optional


# ══════════════════════════════════════════════════════════════════════
# 数据结构
# ══════════════════════════════════════════════════════════════════════

@dataclass
class MeasurementPoint:
    """单个压力点的原始测量值"""
    pressure_psia:   float
    capacitance_pF:  float
    timestamp:       float
    mercury_density: float = None  # None=无实测记录

@dataclass
class AnalysisResult:
    """单个压力点的计算结果（原始值，不做修正）"""
    pressure_psia:          float
    pore_diameter_nm:       float
    cumulative_volume_mLg:  float
    incremental_volume_mLg: float  # 退汞段为负值，原样保留
    mercury_density_gmL:    float
    capacitance_pF:         float

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
class SMPFile:
    """SMP 文件完整解析结果"""
    file_path:        str = ""
    version:          str = ""
    created:          str = ""
    modified:         str = ""

    penetrometer:     PenetrometerInfo = field(default_factory=PenetrometerInfo)
    instrument_name:  str   = ""
    instrument_model: str   = ""
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

    material: MaterialInfo = field(default_factory=MaterialInfo)

    raw_points:       List[MeasurementPoint] = field(default_factory=list)
    lp_buffer_points: List[MeasurementPoint] = field(default_factory=list)
    pressure_program: List[PressureStep]     = field(default_factory=list)
    event_log:        List[str]              = field(default_factory=list)

    _density_map:             Dict[float, float] = field(default_factory=dict)
    _raw_true_density_stored: float = 0.0

    @property
    def recovered_true_density_gmL(self) -> float:
        """修正 MicroActive bug 后的真实真密度（= 存储值 - 样品质量）"""
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

        if data[2:11] != self.MAGIC:
            raise ValueError("不是有效的 SMP 文件")

        smp = SMPFile()
        smp.file_path = os.path.abspath(filepath)
        self._parse_header(data, smp)

        blocks = self._parse_directory(data)

        if 0x0272 in blocks: self._parse_subset626(data, blocks[0x0272], smp)
        if 0x0273 in blocks: self._parse_subset627(data, blocks[0x0273], smp)
        if 0x0274 in blocks: self._parse_subset628(data, blocks[0x0274], smp)
        if 0x0276 in blocks: self._parse_subset630(data, blocks[0x0276], smp)
        if 0x027A in blocks: self._parse_subset634(data, blocks[0x027A], smp)
        if 0x02C1 in blocks: self._parse_subset705(data, blocks[0x02C1], smp)
        if 0x0064 in blocks: self._parse_subset100(data, blocks[0x0064], smp)

        self._parse_lp_buffer(data, blocks.get(0x0276, (30423, 2523))[0], smp)

        return smp

    def calculate(self, smp: SMPFile,
                  contact_angle_deg: float = None,
                  surface_tension_dynes_cm: float = None) -> List[AnalysisResult]:
        """
        从原始电容计算孔体积和孔径。
        输出原始值：退汞段增量为负值，原样保留，不做截断。
        """
        angle   = contact_angle_deg or smp.adv_contact_angle_deg
        tension = surface_tension_dynes_cm or smp.surface_tension_dynes_cm
        k       = smp.penetrometer.constant_uL_per_pF
        mass    = smp.sample_mass_g

        if k == 0:    raise ValueError("穿透计常数为 0")
        if mass == 0: raise ValueError("样品质量为 0")

        results, prev_cap = [], None
        for pt in smp.raw_points:
            vol_mLg  = pt.capacitance_pF * k / 1000.0 / mass
            incr_mLg = (pt.capacitance_pF - prev_cap) * k / 1000.0 / mass \
                       if prev_cap is not None else 0.0
            results.append(AnalysisResult(
                pressure_psia          = pt.pressure_psia,
                pore_diameter_nm       = _washburn(pt.pressure_psia, angle, tension),
                cumulative_volume_mLg  = vol_mLg,
                incremental_volume_mLg = incr_mLg,
                mercury_density_gmL    = pt.mercury_density,
                capacitance_pF         = pt.capacitance_pF,
            ))
            prev_cap = pt.capacitance_pF
        return results

    # ── 私有解析方法 ──────────────────────────────────────────────

    def _parse_header(self, data, smp):
        smp.version  = data[12:16].decode("ascii", errors="ignore").strip("\x00")
        fmt = "%Y-%m-%d %H:%M:%S"
        smp.created  = datetime.datetime.fromtimestamp(
            struct.unpack_from("<I", data, 16)[0]).strftime(fmt)
        smp.modified = datetime.datetime.fromtimestamp(
            struct.unpack_from("<I", data, 20)[0]).strftime(fmt)

    def _parse_directory(self, data):
        pos = data.find(b'SUBSET101')
        if pos < 0: return {}
        n = struct.unpack_from("<H", data, pos + 19)[0]
        blocks = {}
        for i in range(min(n, 50)):
            base    = pos + 21 + i * 10
            type_id = struct.unpack_from("<H", data, base)[0]
            offset  = struct.unpack_from("<I", data, base + 2)[0]
            size    = struct.unpack_from("<I", data, base + 6)[0]
            if 0 < offset < len(data) and 0 < size <= len(data) - offset:
                blocks[type_id] = (offset, size)
        return blocks

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
          [density double(8B)] [flag uint8(1B)] [temperature double(8B)]
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

        # 汞密度记录：逐字节扫描找起点
        density_map = {}
        i = 0
        payload = data[o:o+size]
        while i + 17 <= len(payload):
            d    = struct.unpack_from("<d", payload, i)[0]
            flag = payload[i + 8]
            temp = struct.unpack_from("<d", payload, i + 9)[0]
            if flag == 1 and 13.0 < d < 14.0 and 0 < temp < 100:
                break
            i += 1
        while i + 17 <= len(payload):
            d    = struct.unpack_from("<d", payload, i)[0]
            flag = payload[i + 8]
            temp = struct.unpack_from("<d", payload, i + 9)[0]
            if flag == 171: break
            if flag == 1 and 13.0 < d < 14.0 and 0 < temp < 100:
                density_map[round(temp, 2)] = d  # 以温度为key存储
            i += 17
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
                5.0 <= constant <= 20.0
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
                f"{r.capacitance_pF:.6f}",
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
