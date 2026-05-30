"""
microactive_output.py
=====================

Generate a MicroActive-like tabular CSV from an AutoPore SMP file.

Reproduced behaviors:
  1. LP -> HP transition filtering.
  2. HP head-pressure correction.
  3. Extrusion cumulative-volume monotonic constraint.
  4. Percent values use the cumulative volume at max pressure.
  5. Smooth differential intrusion is calculated using the MicroActive-style
     procedure:

       cumulative intrusion -> Akima interpolation on an evenly spaced
       log10(diameter) grid -> 9-point smoothed first derivative -> Akima
       interpolation back to the original pore diameters.
"""

from __future__ import annotations

import csv
import math
from pathlib import Path
from typing import Iterable

import numpy as np
from scipy.interpolate import Akima1DInterpolator
from scipy.signal import savgol_filter

from smp_parser import SMPParser, _washburn


SMOOTH_LOG_GRID_INTERVALS = 249
SMOOTH_DERIVATIVE_WINDOW = 9
SMOOTH_DERIVATIVE_POLYORDER = 2


def _find_hp_start(points) -> int | None:
    """Find the raw-point index where HP analysis starts."""
    for i in range(1, len(points)):
        previous = points[i - 1]
        current = points[i]
        if current.timestamp < previous.timestamp * 0.01 and previous.timestamp > 1000:
            return i
    return None


def _correct_pressure(point, is_hp: bool, k_uL_per_pF: float, stem_mL: float, max_head_psia: float) -> float:
    """Apply MicroActive-like head-pressure correction to HP points."""
    if not is_hp:
        return point.pressure_psia

    intrusion_mL = point.capacitance_pF * k_uL_per_pF / 1000.0
    remaining = max(0.0, 1.0 - intrusion_mL / stem_mL)
    return point.pressure_psia + max_head_psia * remaining


def _max_pressure_raw_index(points, hp_start, lp_max_pressure, k_uL_per_pF, stem_mL, max_head_psia) -> int:
    """Return the original raw-point index of the maximum corrected pressure."""
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
        raise ValueError("No valid pressure points found.")

    return max(valid, key=lambda item: item[1])[0]


def _build_rows(smp) -> list[dict[str, float]]:
    k = smp.penetrometer.constant_uL_per_pF
    mass = smp.sample_mass_g
    stem = smp.penetrometer.stem_volume_mL
    max_head = smp.penetrometer.max_head_psia
    theta = smp.adv_contact_angle_deg
    gamma = smp.surface_tension_dynes_cm
    points = smp.raw_points

    if k == 0:
        raise ValueError("Penetrometer constant is zero.")
    if mass == 0:
        raise ValueError("Sample mass is zero.")

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

        # Clamp extrusion cumulative volume so reported values do not exceed 100%.
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
        raise ValueError("No rows generated from SMP data.")

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


def _interval_log_slopes(rows: list[dict[str, float]]) -> list[float]:
    slopes = []
    for previous, current in zip(rows, rows[1:]):
        d0 = previous["diameter"]
        d1 = current["diameter"]
        if d0 > 0 and d1 > 0 and d0 != d1:
            delta_log = abs(math.log10(d1) - math.log10(d0))
            slopes.append((current["cum_vol"] - previous["cum_vol"]) / delta_log if delta_log else 0.0)
        else:
            slopes.append(0.0)
    return slopes


def _half_cycle_segments(rows: list[dict[str, float]]) -> Iterable[tuple[int, int]]:
    """Yield row ranges for intrusion and extrusion half-cycles."""
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
    """Return unsmoothed dV/dD and dV/dlog10(D), assigned to interval endpoints."""
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
    for x, y in zip(sorted_x, sorted_y):
        if unique_x and x == unique_x[-1]:
            unique_y[-1] = y
        else:
            unique_x.append(float(x))
            unique_y.append(float(y))

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
    indexes = np.arange(start, stop, dtype=float)
    offsets = indexes - float(center_index)
    design = np.vstack([np.ones_like(offsets), offsets]).T
    coefficients = np.linalg.lstsq(design, y_values[start:stop], rcond=None)[0]
    return float(coefficients[1])


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
    """Add raw and MicroActive-smoothed differential columns."""
    for start, stop in _half_cycle_segments(rows):
        _add_microactive_smooth_differentials(rows[start:stop])


def _write_csv(rows: Iterable[dict[str, float]], output_csv: str) -> None:
    with open(output_csv, "w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "Pressure (psia)",
                "Pore Diameter (nm)",
                "Cumulative Pore Volume (mL/g)",
                "Incremental Pore Volume (mL/g)",
                "Differential Intrusion Raw (/nm*mL/g)",
                "Differential Intrusion (/nm*mL/g)",
                "Log Differential Intrusion Raw (mL/g)",
                "Log Differential Intrusion (mL/g)",
                "% of Total Intrusion Volume",
                "% Incremental Intrusion Volume",
            ]
        )
        for row in rows:
            writer.writerow(
                [
                    f"{row['pressure']:.9f}",
                    f"{row['diameter']:.7f}",
                    f"{row['cum_vol']:.9f}",
                    f"{row['incr_vol']:.9f}",
                    f"{row['diff_intrusion_raw']:.12g}",
                    f"{row['diff_intrusion_smooth']:.12g}",
                    f"{row['log_diff_intrusion_raw']:.9f}",
                    f"{row['log_diff_intrusion_smooth']:.9f}",
                    f"{row['pct_total']:.9f}",
                    f"{row['pct_incr']:.9f}",
                ]
            )


def generate_microactive_output(filepath: str, output_csv: str = "microactive_output.csv") -> list[dict[str, float]]:
    parser = SMPParser()
    smp = parser.parse(filepath)

    rows = _build_rows(smp)
    _add_percentages(rows)
    _add_differentials(rows)
    _write_csv(rows, output_csv)

    max_pressure_index = max(range(len(rows)), key=lambda i: rows[i]["pressure"])
    print(f"Exported: {output_csv} ({len(rows)} rows)")
    print(f"  Total intrusion volume: {rows[max_pressure_index]['cum_vol']:.6f} mL/g")
    print(f"  Max pressure: {rows[max_pressure_index]['pressure']:.2f} psia")
    print(
        "  Smooth differential method: "
        f"Akima + {SMOOTH_LOG_GRID_INTERVALS + 1} log10(D) grid points + "
        f"{SMOOTH_DERIVATIVE_WINDOW}-point derivative"
    )

    return rows


if __name__ == "__main__":
    smp_file = next(Path(".").glob("铂源压汞.SMP"), None)
    if smp_file is None:
        raise FileNotFoundError("No 008-804.SMP file found in the current directory.")

    generate_microactive_output(str(smp_file), "microactive_output.csv")
