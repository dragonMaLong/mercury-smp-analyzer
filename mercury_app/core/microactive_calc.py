from __future__ import annotations

import csv
import math
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import numpy as np
from scipy.interpolate import Akima1DInterpolator
from scipy.signal import savgol_filter

from .models import MercuryResult
from .smp_parser import BlankCurve, SMPFile, blank_curve_value, parse_smp, _washburn


SMOOTH_LOG_GRID_INTERVALS = 249
SMOOTH_DERIVATIVE_WINDOW = 9
SMOOTH_DERIVATIVE_POLYORDER = 2


def load_smp(filepath: str | Path) -> MercuryResult:
    """解析 SMP 文件并返回计算结果。"""
    smp = parse_smp(filepath)
    return calculate_microactive(smp)


def calculate_microactive(
    smp: SMPFile,
    *,
    adv_contact_angle_deg: float | None = None,
    surface_tension_dynes_cm: float | None = None,
) -> MercuryResult:
    """根据已解析的 SMP 文件计算类 MicroActive 孔结构数据。

    空白校正取 ``smp.blank_selection``："file" 使用文件自身的设置（含内嵌空白
    曲线时扣除），None 不校正，或由 blank_curve_from_smp 生成的外部空白曲线。
    """
    theta = smp.adv_contact_angle_deg if adv_contact_angle_deg is None else float(adv_contact_angle_deg)
    gamma = smp.surface_tension_dynes_cm if surface_tension_dynes_cm is None else float(surface_tension_dynes_cm)
    blank = _selected_blank(smp)
    rows = _build_rows(smp, theta, gamma, blank)
    _add_percentages(rows)
    _add_differentials(rows)
    metadata = _metadata_from_smp(
        smp,
        adv_contact_angle_deg=theta,
        surface_tension_dynes_cm=gamma,
    )
    embedded = smp.blank_curve
    metadata["blank_embedded_label"] = embedded.label if embedded is not None else ""
    metadata["blank_correction_label"] = blank.label if blank is not None else ""
    metadata["blank_correction_source"] = (
        "none" if blank is None else "file" if blank is embedded else "external")
    if blank is not None:
        origin = "文件内嵌空白曲线" if blank is embedded else "所选空白文件"
        metadata["warnings"].append(
            f"已按{origin}（{blank.label}）扣除空白体积，进/退汞分支分别插值。")
        if smp.measurement_basis == "capacitance_pF":
            metadata["warnings"].append(
                "9600 系列文件的进汞段空白扣除已与 MicroActive 7.00 逐点核对；"
                "退汞段按对应压力只扣一次空白；原厂软件（9600 2.03、MicroActive 7.00）在该段逐点累加空白，两者不同。")
        else:
            zero_lp = sum(
                1 for p in smp.raw_points[: smp.intrusion_point_count]
                if p.pressure_psia > 0 and p.intrusion_volume_mL == 0
            )
            if zero_lp >= 5:
                metadata["warnings"].append(
                    f"进汞段有 {zero_lp} 个点原始体积恰为 0，这些点的累计体积完全来自空白扣除，解释低压孔隙时需谨慎。")
    elif embedded is not None:
        metadata["warnings"].append(f"文件设置了空白校正（{embedded.label}），当前选择为不校正。")
    if max(rows, key=lambda row: row["pressure"])["cum_vol"] <= 0:
        metadata["warnings"].append("总进汞量≤0，孔隙率、骨架密度及相关孔结构指标不可计算。")
        if smp.measurement_basis == "intrusion_volume_mL":
            for row in rows:
                row["pct_total"] = row["pct_incr"] = float("nan")
    return MercuryResult.from_rows(
        metadata,
        rows,
        raw_smp=smp,
    )


def _selected_blank(smp: SMPFile) -> BlankCurve | None:
    selection = getattr(smp, "blank_selection", "file")
    if selection is None:
        return None
    if isinstance(selection, BlankCurve):
        return selection
    if selection == "file":
        return smp.blank_curve
    raise ValueError(f"无效的空白校正选择: {selection!r}")


def blank_curve_from_smp(filepath: str | Path) -> BlankCurve:
    """用另一个 SMP 文件作为空白：取其未校正的比体积（mL/g，按该文件自身质量）。

    与 MicroActive 7.00 的 "Select/Replace Blank" 一致（旧版与 9600 文件均已核对进汞段）。
    """
    smp = parse_smp(filepath)
    rows = _build_rows(smp, smp.adv_contact_angle_deg, smp.surface_tension_dynes_cm, None)
    intrusion_count = sum(1 for row in rows if not row["is_extrusion"])
    if intrusion_count < 2:
        raise ValueError("空白文件的进汞点不足，无法用于校正。")
    return BlankCurve(
        reference_path=str(filepath),
        points=[(row["pressure"], row["cum_vol"]) for row in rows],
        intrusion_count=intrusion_count,
        per_gram=True,
    )


def _subtract_blank(rows: list[dict[str, float]], blank: BlankCurve | None, mass: float) -> None:
    if blank is None:
        return
    scale = 1.0 if blank.per_gram else 1.0 / mass
    for index, row in enumerate(rows):
        row["cum_vol"] -= blank_curve_value(blank, row["pressure"], bool(row["is_extrusion"])) * scale
        row["incr_vol"] = row["cum_vol"] - rows[index - 1]["cum_vol"] if index else 0.0


def export_microactive_csv(result: MercuryResult, output_csv: str | Path) -> None:
    """导出计算数据表。"""
    with open(output_csv, "w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "压力 (psia)",
                "孔径 (nm)",
                "累计孔体积 (mL/g)",
                "增量孔体积 (mL/g)",
                "原始微分入汞 (/nm*mL/g)",
                "平滑微分入汞 (/nm*mL/g)",
                "原始对数微分入汞 (mL/g)",
                "平滑对数微分入汞 (mL/g)",
                "总入汞体积占比 (%)",
                "增量入汞体积占比 (%)",
            ]
        )
        for row in result.table:
            writer.writerow(
                [
                    f"{row['pressure']:.9f}",
                    f"{row['diameter']:.7f}",
                    f"{row['cum_volume']:.9f}",
                    f"{row['incremental_volume']:.9f}",
                    f"{row['diff_intrusion_raw']:.12g}",
                    f"{row['diff_intrusion_smooth']:.12g}",
                    f"{row['log_diff_intrusion_raw']:.9f}",
                    f"{row['log_diff_intrusion_smooth']:.9f}",
                    f"{row['pct_total']:.9f}",
                    f"{row['pct_incremental']:.9f}",
                ]
            )


def _metadata_from_smp(
    smp: SMPFile,
    *,
    adv_contact_angle_deg: float | None = None,
    surface_tension_dynes_cm: float | None = None,
) -> dict[str, Any]:
    file_path = Path(smp.file_path)
    theta = smp.adv_contact_angle_deg if adv_contact_angle_deg is None else adv_contact_angle_deg
    gamma = smp.surface_tension_dynes_cm if surface_tension_dynes_cm is None else surface_tension_dynes_cm
    return {
        "file_path": smp.file_path,
        "file_name": file_path.stem,
        "sample_name": smp.sample_name,
        "operator": smp.operator,
        "submitter": smp.submitter,
        "bar_code": smp.bar_code,
        "created": smp.created,
        "modified": smp.modified,
        "version": smp.version,
        "instrument_model": smp.instrument_model,
        "instrument_name": _instrument_name(smp.instrument_name, smp.instrument_model)
        + (f"（序列号 {smp.instrument_serial}）"
           if smp.measurement_basis == "intrusion_volume_mL" and smp.instrument_serial else ""),
        "instrument_serial": smp.instrument_serial,
        "instrument_ports": smp.instrument_ports,
        "analysis_software": smp.analysis_software,
        "analysis_software_version": smp.analysis_software_version,
        "software_version": smp.software_version,
        "sample_mass_g": smp.sample_mass_g,
        "assembly_mass_g": smp.assembly_mass_g,
        "mercury_mass_g": smp.mercury_mass_g,
        "mercury_temperature_C": smp.mercury_temperature_C,
        "mercury_density_gmL": smp.mercury_density_gmL,
        "adv_contact_angle_deg": theta,
        "rec_contact_angle_deg": smp.rec_contact_angle_deg,
        "surface_tension_dynes_cm": gamma,
        "smp_adv_contact_angle_deg": smp.adv_contact_angle_deg,
        "smp_surface_tension_dynes_cm": smp.surface_tension_dynes_cm,
        "adv_contact_angle_is_override": not np.isclose(theta, smp.adv_contact_angle_deg),
        "surface_tension_is_override": not np.isclose(gamma, smp.surface_tension_dynes_cm),
        "material_name": smp.material.name,
        "bet_surface_area_m2g": smp.material.bet_surface_area_m2g,
        "bulk_density_gmL": smp.material.bulk_density_gmL,
        "true_density_gmL": smp.recovered_true_density_gmL,
        "penetrometer_model": smp.penetrometer.model,
        "penetrometer_constant_uL_per_pF": smp.penetrometer.constant_uL_per_pF,
        "penetrometer_mass_g": smp.penetrometer.mass_g,
        "penetrometer_bulb_volume_mL": smp.penetrometer.bulb_volume_mL,
        "penetrometer_stem_volume_mL": smp.penetrometer.stem_volume_mL,
        "penetrometer_max_head_psia": smp.penetrometer.max_head_psia,
        "raw_point_count": len(smp.raw_points),
        "lp_buffer_point_count": len(smp.lp_buffer_points),
        "pressure_program_count": len(smp.pressure_program),
        "measurement_basis": smp.measurement_basis,
        "warnings": list(smp.warnings),
    }


def _instrument_name(instrument_name: str, instrument_model: str) -> str:
    name = str(instrument_name or "").strip()
    if name:
        return name
    model = str(instrument_model or "").strip()
    if not model:
        return ""
    if "autopore" in model.lower():
        return model
    if "9600" in model:
        return f"AutoPore {model}"
    return model


def _find_hp_start(points) -> int | None:
    for i in range(1, len(points)):
        previous = points[i - 1]
        current = points[i]
        if current.timestamp < previous.timestamp * 0.01 and previous.timestamp > 1000:
            return i
    return None


def _correct_pressure(point, is_hp: bool, k_uL_per_pF: float, stem_mL: float, max_head_psia: float) -> float:
    if not is_hp:
        return point.pressure_psia

    intrusion_mL = point.capacitance_pF * k_uL_per_pF / 1000.0
    remaining = max(0.0, 1.0 - intrusion_mL / stem_mL)
    return point.pressure_psia + max_head_psia * remaining


def _max_pressure_raw_index(points, hp_start, lp_max_pressure, k_uL_per_pF, stem_mL, max_head_psia) -> int:
    valid = []
    hp_valid_started = False

    for i, point in enumerate(points):
        is_hp = hp_start is not None and i >= hp_start
        pressure = _correct_pressure(point, is_hp, k_uL_per_pF, stem_mL, max_head_psia)

        if is_hp and not hp_valid_started:
            if pressure <= lp_max_pressure:
                continue
            hp_valid_started = True

        valid.append((i, pressure))

    if not valid:
        raise ValueError("没有找到有效压力点。")

    return max(valid, key=lambda item: item[1])[0]


def _build_rows(
    smp: SMPFile, theta: float, gamma: float, blank: BlankCurve | None = None,
) -> list[dict[str, float]]:
    if smp.measurement_basis == "intrusion_volume_mL":
        rows = _build_volume_rows(smp, theta, gamma)
        _subtract_blank(rows, blank, smp.sample_mass_g)
        return rows
    if smp.measurement_basis != "capacitance_pF":
        raise ValueError(f"不支持的测量单位: {smp.measurement_basis}")

    k = smp.penetrometer.constant_uL_per_pF
    mass = smp.sample_mass_g
    stem = smp.penetrometer.stem_volume_mL
    max_head = smp.penetrometer.max_head_psia
    points = smp.raw_points

    if k == 0:
        raise ValueError("膨胀计常数为 0。")
    if mass == 0:
        raise ValueError("样品质量为 0。")

    hp_start = _find_hp_start(points)
    lp_max_pressure = points[hp_start - 1].pressure_psia if hp_start else 0.0
    max_raw_index = _max_pressure_raw_index(points, hp_start, lp_max_pressure, k, stem, max_head)

    rows = []
    previous_capacitance = None
    hp_valid_started = False

    for i, point in enumerate(points):
        is_hp = hp_start is not None and i >= hp_start
        is_extrusion = i > max_raw_index
        pressure = _correct_pressure(point, is_hp, k, stem, max_head)

        if is_hp and not hp_valid_started:
            if pressure <= lp_max_pressure:
                continue
            hp_valid_started = True

        cumulative = point.capacitance_pF * k / 1000.0 / mass
        if previous_capacitance is None:
            incremental = 0.0
        else:
            incremental = (point.capacitance_pF - previous_capacitance) * k / 1000.0 / mass

        if is_extrusion and rows:
            cumulative = min(cumulative, rows[-1]["cum_vol"])
            incremental = cumulative - rows[-1]["cum_vol"]

        rows.append(
            {
                "pressure": pressure,
                "diameter": _washburn(pressure, theta, gamma),
                "cum_vol": cumulative,
                "incr_vol": incremental,
                "is_extrusion": 1.0 if is_extrusion else 0.0,
            }
        )
        previous_capacitance = point.capacitance_pF

    if not rows:
        raise ValueError("未能从 SMP 数据生成计算表。")

    _subtract_blank(rows, blank, mass)
    return rows


def _build_volume_rows(smp: SMPFile, theta: float, gamma: float) -> list[dict[str, float]]:
    """Legacy IV records already contain head-corrected psia and absolute mL.

    Stored volumes are not blank-corrected; _build_rows subtracts the selected
    blank afterwards. Do not reapply the V-series capacitance conversion,
    pressure correction, or extrusion envelope here.
    """
    mass = smp.sample_mass_g
    if not math.isfinite(mass) or mass <= 0:
        raise ValueError("旧版 SMP 样品质量必须为有限正数。")
    boundary = smp.intrusion_point_count
    if boundary is None or not 0 < boundary <= len(smp.raw_points):
        raise ValueError("旧版 SMP 的进退汞分界无效。")

    rows = []
    for index, point in enumerate(smp.raw_points):
        pressure = point.pressure_psia
        volume = point.intrusion_volume_mL
        if not math.isfinite(pressure) or volume is None or not math.isfinite(volume):
            raise ValueError("旧版 SMP 包含无效压力或体积记录。")
        if pressure <= 0:
            continue
        cumulative = volume / mass
        rows.append({
            "pressure": pressure,
            "diameter": _washburn(pressure, theta, gamma),
            "cum_vol": cumulative,
            "incr_vol": cumulative - rows[-1]["cum_vol"] if rows else 0.0,
            "is_extrusion": float(index >= boundary),
        })
    if not rows:
        raise ValueError("旧版 SMP 没有可计算的正压力点。")
    return rows


def _add_percentages(rows: list[dict[str, float]]) -> None:
    max_pressure_row = max(rows, key=lambda row: row["pressure"])
    total_volume = max_pressure_row["cum_vol"]

    for row in rows:
        if total_volume > 0:
            row["pct_total"] = row["cum_vol"] / total_volume * 100.0
            row["pct_incr"] = row["incr_vol"] / total_volume * 100.0
        else:
            row["pct_total"] = 0.0
            row["pct_incr"] = 0.0


def _half_cycle_segments(rows: list[dict[str, float]]) -> Iterable[tuple[int, int]]:
    if not rows:
        return

    start = 0
    current_flag = rows[0]["is_extrusion"]
    for i, row in enumerate(rows[1:], start=1):
        if row["is_extrusion"] != current_flag:
            yield start, i
            start = i
            current_flag = row["is_extrusion"]

    yield start, len(rows)


def _raw_interval_differentials(segment: list[dict[str, float]]) -> tuple[list[float], list[float]]:
    raw_diff = [0.0] * len(segment)
    raw_log = [0.0] * len(segment)

    for i in range(1, len(segment)):
        previous = segment[i - 1]
        current = segment[i]
        d0 = previous["diameter"]
        d1 = current["diameter"]
        delta_volume = current["cum_vol"] - previous["cum_vol"]

        if d0 > 0 and d1 > 0 and d0 != d1:
            raw_diff[i] = delta_volume / abs(d1 - d0)
            raw_log[i] = delta_volume / abs(math.log10(d1) - math.log10(d0))

    return raw_diff, raw_log


def _unique_sorted_xy(x_values: np.ndarray, y_values: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    order = np.argsort(x_values)
    sorted_x = x_values[order]
    sorted_y = y_values[order]

    unique_x = []
    unique_y = []
    for x_value, y_value in zip(sorted_x, sorted_y):
        if unique_x and x_value == unique_x[-1]:
            unique_y[-1] = y_value
        else:
            unique_x.append(float(x_value))
            unique_y.append(float(y_value))

    return np.array(unique_x), np.array(unique_y)


def _akima_interpolate(x_values: np.ndarray, y_values: np.ndarray, target_x: np.ndarray) -> np.ndarray:
    x_unique, y_unique = _unique_sorted_xy(x_values, y_values)
    if len(x_unique) < 2:
        return np.zeros_like(target_x, dtype=float)
    if len(x_unique) < 5:
        return np.interp(target_x, x_unique, y_unique)

    interpolator = Akima1DInterpolator(x_unique, y_unique, method="akima")
    return np.asarray(interpolator(target_x), dtype=float)


def _local_linear_derivative(y_values: np.ndarray, center_index: int, start: int, stop: int) -> float:
    # Closed-form least-squares slope: independent of the LAPACK backend, and
    # exactly zero over a flat window.
    offsets = np.arange(start, stop, dtype=float) - float(center_index)
    centered = offsets - offsets.mean()
    denominator = float(np.sum(centered**2))
    if denominator == 0.0:
        return 0.0
    values = y_values[start:stop] - y_values[center_index]
    return float(np.sum(centered * values) / denominator)


def _anchored_linear_derivative(y_values: np.ndarray, center_index: int, start: int, stop: int) -> float:
    indexes = np.arange(start, stop, dtype=float)
    offsets = indexes - float(center_index)
    values = y_values[start:stop]
    mask = offsets != 0.0
    denominator = float(np.sum(offsets[mask] ** 2))
    if denominator == 0.0:
        return 0.0
    return float(np.sum(offsets[mask] * (values[mask] - y_values[center_index])) / denominator)


def _nine_point_smoothed_derivative(y_values: np.ndarray) -> np.ndarray:
    derivative = savgol_filter(
        y_values,
        SMOOTH_DERIVATIVE_WINDOW,
        SMOOTH_DERIVATIVE_POLYORDER,
        deriv=1,
        delta=1.0,
        mode="interp",
    )
    # A flat window has an exact zero slope; the filter coefficients only sum
    # to zero within rounding, which depends on the LAPACK backend.
    if len(y_values) >= SMOOTH_DERIVATIVE_WINDOW:
        windows = np.lib.stride_tricks.sliding_window_view(y_values, SMOOTH_DERIVATIVE_WINDOW)
        flat = np.flatnonzero(np.ptp(windows, axis=1) == 0) + SMOOTH_DERIVATIVE_WINDOW // 2
        derivative[flat] = 0.0

    edge_count = min(SMOOTH_DERIVATIVE_WINDOW // 2, len(y_values))
    for i in range(edge_count):
        if i == 0:
            derivative[i] = _anchored_linear_derivative(y_values, i, 0, min(len(y_values), edge_count + 1))
        else:
            derivative[i] = _local_linear_derivative(y_values, i, 0, min(len(y_values), i + edge_count))

    for i in range(len(y_values) - edge_count, len(y_values)):
        derivative[i] = _local_linear_derivative(y_values, i, max(0, i - edge_count), len(y_values))

    return derivative


def _add_microactive_smooth_differentials(segment: list[dict[str, float]]) -> None:
    raw_diff, raw_log = _raw_interval_differentials(segment)

    for row, diff_value, log_value in zip(segment, raw_diff, raw_log):
        row["diff_intrusion_raw"] = diff_value
        row["log_diff_intrusion_raw"] = log_value
        row["diff_intrusion_smooth"] = diff_value
        row["log_diff_intrusion_smooth"] = log_value

    if len(segment) <= SMOOTH_DERIVATIVE_WINDOW - 1:
        return

    diameters = np.array([row["diameter"] for row in segment], dtype=float)
    cumulative = np.array([row["cum_vol"] for row in segment], dtype=float)
    if np.any(diameters <= 0) or not np.all(np.isfinite(diameters)) or not np.all(np.isfinite(cumulative)):
        return

    log_diameter = np.log10(diameters)
    if log_diameter.max() == log_diameter.min():
        return

    grid_x = np.linspace(log_diameter.min(), log_diameter.max(), SMOOTH_LOG_GRID_INTERVALS + 1)
    grid_h = grid_x[1] - grid_x[0]
    grid_diameter = 10.0**grid_x

    grid_cumulative = _akima_interpolate(log_diameter, cumulative, grid_x)
    derivative_per_grid_step = _nine_point_smoothed_derivative(grid_cumulative)

    path_sign = 1.0 if log_diameter[-1] > log_diameter[0] else -1.0
    grid_log_diff = path_sign * derivative_per_grid_step / grid_h

    log_to_linear_factor = (10.0**grid_h - 1.0) / grid_h
    grid_diff = grid_log_diff / (grid_diameter * log_to_linear_factor)

    smooth_log = _akima_interpolate(grid_x, grid_log_diff, log_diameter)
    smooth_diff = _akima_interpolate(grid_x, grid_diff, log_diameter)

    for row, diff_value, log_value in zip(segment, smooth_diff, smooth_log):
        row["diff_intrusion_smooth"] = float(diff_value)
        row["log_diff_intrusion_smooth"] = float(log_value)


def _add_differentials(rows: list[dict[str, float]]) -> None:
    for start, stop in _half_cycle_segments(rows):
        _add_microactive_smooth_differentials(rows[start:stop])
