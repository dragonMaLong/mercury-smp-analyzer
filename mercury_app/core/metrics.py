from __future__ import annotations

import numpy as np

from .models import MercuryResult, PoreSummary, SegmentMetrics


def metrics_for_pressure_range(
    result: MercuryResult,
    pressure_min: float,
    pressure_max: float,
    intrusion_only: bool = True,
) -> SegmentMetrics:
    """返回所选压力范围内的孔结构统计参数。"""
    lo, hi = sorted((float(pressure_min), float(pressure_max)))
    mask = np.isfinite(result.pressure) & (result.pressure >= lo) & (result.pressure <= hi)
    if intrusion_only:
        mask &= result.is_extrusion < 0.5

    indexes = np.flatnonzero(mask)
    if indexes.size == 0:
        return SegmentMetrics(
            pressure_min=lo,
            pressure_max=hi,
            diameter_min=float("nan"),
            diameter_max=float("nan"),
            pore_volume=0.0,
            pore_volume_percent=0.0,
            peak_diameter=float("nan"),
            peak_value=float("nan"),
            point_count=0,
        )

    diameters = result.diameter[indexes]
    incremental = result.incremental_volume[indexes]
    log_diff = result.log_diff_intrusion[indexes]

    positive_increments = np.where(np.isfinite(incremental) & (incremental > 0), incremental, 0.0)
    pore_volume = float(np.sum(positive_increments))
    total_volume = result.total_pore_volume
    pore_percent = pore_volume / total_volume * 100.0 if total_volume > 0 else 0.0

    finite_peak_mask = np.isfinite(log_diff)
    if np.any(finite_peak_mask):
        local_peak_index = int(np.nanargmax(np.where(finite_peak_mask, log_diff, -np.inf)))
        peak_diameter = float(diameters[local_peak_index])
        peak_value = float(log_diff[local_peak_index])
    else:
        peak_diameter = float("nan")
        peak_value = float("nan")

    return SegmentMetrics(
        pressure_min=lo,
        pressure_max=hi,
        diameter_min=float(np.nanmin(diameters)),
        diameter_max=float(np.nanmax(diameters)),
        pore_volume=pore_volume,
        pore_volume_percent=pore_percent,
        peak_diameter=peak_diameter,
        peak_value=peak_value,
        point_count=int(indexes.size),
    )


def summary_metrics(result: MercuryResult, bulk_density_pressure: float = 0.5) -> PoreSummary:
    """返回类 MicroActive 的整样品汇总参数。"""
    total_volume = result.total_pore_volume
    total_pressure = result.max_pressure
    total_area = _total_pore_area(result)
    median_volume_pressure, median_volume_diameter = _median_by_cumulative_volume(result, total_volume / 2.0)
    median_area_pressure, median_area_diameter = _median_by_cumulative_area(result, total_area / 2.0)
    average_diameter = 4000.0 * total_volume / total_area if total_area > 0 else float("nan")
    bulk_density = _bulk_density_at_pressure(result, bulk_density_pressure)

    apparent_density = float("nan")
    porosity = float("nan")
    if np.isfinite(bulk_density) and bulk_density > 0 and total_volume > 0:
        denominator = (1.0 / bulk_density) - total_volume
        if denominator > 0:
            apparent_density = 1.0 / denominator
            porosity = (1.0 - bulk_density / apparent_density) * 100.0

    return PoreSummary(
        total_intrusion_pressure=total_pressure,
        total_intrusion_volume=total_volume,
        total_pore_area=total_area,
        median_volume_pressure=median_volume_pressure,
        median_volume_diameter=median_volume_diameter,
        median_volume=total_volume / 2.0,
        median_area_pressure=median_area_pressure,
        median_area_diameter=median_area_diameter,
        median_area=total_area / 2.0,
        average_pore_diameter=average_diameter,
        bulk_density_pressure=bulk_density_pressure,
        bulk_density=bulk_density,
        apparent_density_pressure=total_pressure,
        apparent_density=apparent_density,
        porosity=porosity,
    )


def _intrusion_indexes(result: MercuryResult) -> np.ndarray:
    return np.flatnonzero(
        np.isfinite(result.pressure)
        & (result.pressure > 0)
        & np.isfinite(result.diameter)
        & (result.diameter > 0)
        & np.isfinite(result.cum_volume)
        & (result.is_extrusion < 0.5)
    )


def _area_intervals(result: MercuryResult) -> list[tuple[int, int, float, float]]:
    indexes = _intrusion_indexes(result)
    intervals = []
    for previous_index, current_index in zip(indexes[:-1], indexes[1:]):
        delta_volume = result.cum_volume[current_index] - result.cum_volume[previous_index]
        if not np.isfinite(delta_volume) or delta_volume <= 0:
            continue
        diameter_midpoint = (result.diameter[previous_index] + result.diameter[current_index]) / 2.0
        if not np.isfinite(diameter_midpoint) or diameter_midpoint <= 0:
            continue
        area = 4000.0 * delta_volume / diameter_midpoint
        intervals.append((int(previous_index), int(current_index), float(delta_volume), float(area)))
    return intervals


def _total_pore_area(result: MercuryResult) -> float:
    return float(sum(area for _, _, _, area in _area_intervals(result)))


def _median_by_cumulative_volume(result: MercuryResult, target_volume: float) -> tuple[float, float]:
    indexes = _intrusion_indexes(result)
    if indexes.size == 0 or not np.isfinite(target_volume):
        return float("nan"), float("nan")

    for previous_index, current_index in zip(indexes[:-1], indexes[1:]):
        previous_volume = result.cum_volume[previous_index]
        current_volume = result.cum_volume[current_index]
        if not (np.isfinite(previous_volume) and np.isfinite(current_volume)):
            continue
        lower = min(previous_volume, current_volume)
        upper = max(previous_volume, current_volume)
        if not (lower <= target_volume <= upper):
            continue
        fraction = _safe_fraction(target_volume - previous_volume, current_volume - previous_volume)
        pressure = _interpolate(result.pressure[previous_index], result.pressure[current_index], fraction)
        return pressure, _diameter_from_pressure(result, pressure)

    closest_index = int(indexes[np.nanargmin(np.abs(result.cum_volume[indexes] - target_volume))])
    pressure = float(result.pressure[closest_index])
    return pressure, float(result.diameter[closest_index])


def _median_by_cumulative_area(result: MercuryResult, target_area: float) -> tuple[float, float]:
    if not np.isfinite(target_area):
        return float("nan"), float("nan")

    cumulative_area = 0.0
    for previous_index, current_index, _, area in _area_intervals(result):
        next_area = cumulative_area + area
        if cumulative_area <= target_area <= next_area:
            fraction = _safe_fraction(target_area - cumulative_area, area)
            pressure = _interpolate(result.pressure[previous_index], result.pressure[current_index], fraction)
            return pressure, _diameter_from_pressure(result, pressure)
        cumulative_area = next_area

    return float("nan"), float("nan")


def _bulk_density_at_pressure(result: MercuryResult, pressure: float) -> float:
    mass = _metadata_number(result, "sample_mass_g")
    bulb_volume = _metadata_number(result, "penetrometer_bulb_volume_mL")
    mercury_mass = _metadata_number(result, "mercury_mass_g")
    mercury_density = _metadata_number(result, "mercury_density_gmL")
    inputs = (mass, bulb_volume, mercury_mass, mercury_density)
    if any((not np.isfinite(value)) or value <= 0 for value in inputs):
        return float("nan")

    intrusion_volume = _cumulative_volume_at_pressure(result, pressure) * mass
    sample_envelope_volume = bulb_volume - (mercury_mass / mercury_density) + intrusion_volume
    if sample_envelope_volume <= 0:
        return float("nan")
    return mass / sample_envelope_volume


def _cumulative_volume_at_pressure(result: MercuryResult, pressure: float) -> float:
    indexes = _intrusion_indexes(result)
    if indexes.size == 0:
        return 0.0
    pressures = result.pressure[indexes]
    volumes = result.cum_volume[indexes]
    order = np.argsort(pressures)
    pressures = pressures[order]
    volumes = volumes[order]
    return float(np.interp(float(pressure), pressures, volumes))


def _diameter_from_pressure(result: MercuryResult, pressure: float) -> float:
    if not np.isfinite(pressure) or pressure <= 0:
        return float("nan")
    mask = np.isfinite(result.pressure) & (result.pressure > 0) & np.isfinite(result.diameter) & (result.diameter > 0)
    if not np.any(mask):
        return float("nan")
    washburn_constant = float(np.nanmedian(result.pressure[mask] * result.diameter[mask]))
    return washburn_constant / pressure


def _metadata_number(result: MercuryResult, key: str) -> float:
    try:
        value = float(result.metadata.get(key, float("nan")))
    except (TypeError, ValueError):
        return float("nan")
    return value if np.isfinite(value) else float("nan")


def _interpolate(start: float, stop: float, fraction: float) -> float:
    return float(start + fraction * (stop - start))


def _safe_fraction(numerator: float, denominator: float) -> float:
    if denominator == 0 or not np.isfinite(denominator):
        return 0.0
    return float(numerator / denominator)
