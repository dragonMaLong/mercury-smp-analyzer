from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .metrics import summary_metrics
from .models import MercuryResult


MAYER_STOWE_THETA_NODES = np.asarray(
    [100.0, 110.0, 117.0, 120.0, 130.0, 140.0, 150.0, 160.0, 170.0, 180.0],
    dtype=float,
)
MAYER_STOWE_PACKING_ANGLE_NODES = np.asarray(
    [
        60.0,
        60.63,
        61.0,
        61.46,
        61.94,
        62.0,
        62.13,
        62.68,
        63.0,
        63.17,
        63.53,
        63.84,
        64.0,
        64.04,
        64.12,
        65.0,
        66.0,
        67.0,
        70.0,
        75.0,
        80.0,
        85.0,
        90.0,
    ],
    dtype=float,
)
MAYER_STOWE_K_TABLE = np.asarray(
    [
        [2.89, 2.86, 2.76, 2.65, 2.53, 2.52, 2.49, 2.39, 2.33, 2.30, 2.24, 2.19, 2.17, 2.16, 2.15, 2.03, 1.91, 1.81, 1.59, 1.35, 1.23, 1.16, 1.14],
        [5.09, 4.99, 4.93, 4.85, 4.64, 4.62, 4.57, 4.37, 4.26, 4.20, 4.09, 4.00, 3.95, 3.94, 3.92, 3.70, 3.48, 3.29, 2.87, 2.44, 2.20, 2.08, 2.04],
        [6.37, 6.24, 6.16, 6.06, 5.96, 5.92, 5.87, 5.59, 5.44, 5.37, 5.23, 5.11, 5.05, 5.04, 5.01, 4.71, 4.43, 4.19, 3.64, 3.08, 2.77, 2.61, 2.57],
        [6.87, 6.72, 6.64, 6.53, 6.41, 6.40, 6.37, 6.08, 5.92, 5.84, 5.68, 5.55, 5.48, 5.46, 5.43, 5.12, 4.81, 4.54, 3.94, 3.33, 2.99, 2.82, 2.77],
        [8.32, 8.14, 8.03, 7.90, 7.76, 7.74, 7.70, 7.54, 7.34, 7.25, 7.04, 6.86, 6.78, 6.76, 6.72, 6.32, 5.93, 5.59, 4.83, 4.06, 3.63, 3.42, 3.35],
        [9.46, 9.25, 9.13, 8.97, 8.81, 8.79, 8.75, 8.56, 8.46, 8.40, 8.15, 7.95, 7.86, 7.84, 7.79, 7.31, 6.84, 6.44, 5.54, 4.63, 4.13, 3.88, 3.80],
        [10.32, 10.09, 9.95, 9.79, 9.61, 9.59, 9.54, 9.34, 9.22, 9.16, 9.03, 8.80, 8.70, 8.68, 8.63, 8.08, 7.55, 7.10, 6.08, 5.06, 4.50, 4.21, 4.13],
        [10.90, 10.65, 10.51, 10.33, 10.14, 10.12, 10.07, 9.85, 9.73, 9.66, 9.52, 9.40, 9.29, 9.27, 9.22, 8.62, 8.05, 7.56, 6.46, 5.35, 4.75, 4.43, 4.34],
        [11.22, 10.97, 10.82, 10.64, 10.45, 10.43, 10.38, 10.16, 10.03, 9.96, 9.82, 9.70, 9.64, 9.62, 9.57, 8.94, 8.35, 7.83, 6.68, 5.52, 4.88, 4.56, 4.45],
        [11.32, 11.07, 10.92, 10.74, 10.54, 10.52, 10.47, 10.25, 10.12, 10.05, 9.91, 9.78, 9.72, 9.70, 9.67, 9.04, 8.44, 7.92, 6.75, 5.57, 4.92, 4.59, 4.49],
    ],
    dtype=float,
)

MAYER_STOWE_MIN_POROSITY = 0.2595
MAYER_STOWE_MAX_POROSITY = 0.4763
MAYER_STOWE_DIAMETER_FACTOR_NM_PSIA_PER_DYNE_CM = 290.07547546


def _porosity_from_packing_angle(packing_angle_deg: float) -> float:
    cosine = np.cos(np.deg2rad(float(packing_angle_deg)))
    denominator = 6.0 * np.sqrt(1.0 - 3.0 * cosine**2 + 2.0 * cosine**3)
    return float(1.0 - np.pi / denominator)


MAYER_STOWE_POROSITY_NODES = np.asarray(
    [_porosity_from_packing_angle(value) for value in MAYER_STOWE_PACKING_ANGLE_NODES],
    dtype=float,
)


@dataclass(frozen=True)
class MayerStoweResult:
    pressure_min: float
    pressure_max: float
    selected_pore_volume: float
    bulk_density: float
    interstitial_porosity_raw: float
    interstitial_porosity: float
    breakthrough_pressure_ratio: float
    pressure: np.ndarray = field(repr=False)
    particle_diameter: np.ndarray = field(repr=False)
    cumulative_coarser_percent: np.ndarray = field(repr=False)
    incremental_percent: np.ndarray = field(repr=False)
    status: str = ""
    sample_name: str = ""

    @property
    def is_valid(self) -> bool:
        return (
            not self.status
            and np.isfinite(self.breakthrough_pressure_ratio)
            and self.pressure.size > 1
            and self.particle_diameter.size == self.pressure.size
        )


def lookup_mayer_stowe_k(contact_angle_deg: float, interstitial_porosity: float) -> float:
    """Return AutoPore 9600's Mayer–Stowe pressure ratio by bilinear interpolation."""
    theta = float(contact_angle_deg)
    epsilon = float(interstitial_porosity)
    if not (np.isfinite(theta) and np.isfinite(epsilon)):
        return float("nan")
    if theta < MAYER_STOWE_THETA_NODES[0] or theta > MAYER_STOWE_THETA_NODES[-1]:
        return float("nan")

    effective_epsilon = float(np.clip(epsilon, MAYER_STOWE_MIN_POROSITY, MAYER_STOWE_MAX_POROSITY))
    lookup_epsilon = float(
        np.clip(effective_epsilon, MAYER_STOWE_POROSITY_NODES[0], MAYER_STOWE_POROSITY_NODES[-1])
    )
    values_at_theta_nodes = np.asarray(
        [np.interp(lookup_epsilon, MAYER_STOWE_POROSITY_NODES, row) for row in MAYER_STOWE_K_TABLE],
        dtype=float,
    )
    return float(np.interp(theta, MAYER_STOWE_THETA_NODES, values_at_theta_nodes))


def calculate_mayer_stowe(
    result: MercuryResult,
    pressure_min: float,
    pressure_max: float,
) -> MayerStoweResult:
    """Calculate a Mayer–Stowe distribution for the selected intrusion-pressure range."""
    empty = np.asarray([], dtype=float)
    lo, hi = sorted((float(pressure_min), float(pressure_max)))
    mask = (
        np.isfinite(result.pressure)
        & (result.pressure > 0)
        & np.isfinite(result.cum_volume)
        & (result.is_extrusion < 0.5)
    )
    if not np.any(mask):
        return MayerStoweResult(
            lo, hi, 0.0, float("nan"), float("nan"), float("nan"), float("nan"),
            empty, empty, empty, empty, "没有可用的入汞数据", result.sample_name,
        )

    pressures = np.asarray(result.pressure[mask], dtype=float)
    volumes = np.asarray(result.cum_volume[mask], dtype=float)
    order = np.argsort(pressures)
    pressures = pressures[order]
    volumes = volumes[order]
    pressures, unique_indexes = np.unique(pressures, return_index=True)
    volumes = volumes[unique_indexes]

    lo = max(lo, float(pressures[0]))
    hi = min(hi, float(pressures[-1]))
    if not (np.isfinite(lo) and np.isfinite(hi) and lo < hi):
        return MayerStoweResult(
            lo, hi, 0.0, float("nan"), float("nan"), float("nan"), float("nan"),
            empty, empty, empty, empty, "压力选区无效", result.sample_name,
        )

    inner = pressures[(pressures > lo) & (pressures < hi)]
    selected_pressures = np.concatenate(([lo], inner, [hi]))
    selected_volumes = np.interp(selected_pressures, pressures, volumes)
    volume_low = float(selected_volumes[0])
    volume_high = float(selected_volumes[-1])
    selected_volume = float(volume_high - volume_low)

    summary = summary_metrics(result)
    bulk_density = float(summary.bulk_density)
    if not np.isfinite(bulk_density) or bulk_density <= 0:
        try:
            bulk_density = float(result.metadata.get("bulk_density_gmL", float("nan")))
        except (TypeError, ValueError):
            bulk_density = float("nan")

    epsilon_raw = bulk_density * selected_volume if np.isfinite(bulk_density) else float("nan")
    epsilon = (
        float(np.clip(epsilon_raw, MAYER_STOWE_MIN_POROSITY, MAYER_STOWE_MAX_POROSITY))
        if np.isfinite(epsilon_raw)
        else float("nan")
    )
    theta = float(result.metadata.get("adv_contact_angle_deg", float("nan")))
    gamma = float(result.metadata.get("surface_tension_dynes_cm", float("nan")))
    ratio = lookup_mayer_stowe_k(theta, epsilon)

    if not np.isfinite(bulk_density) or bulk_density <= 0:
        status = "缺少有效体积密度"
    elif selected_volume <= 0:
        status = "选区孔容必须大于 0"
    elif not np.isfinite(ratio):
        status = "接触角须在 100°–180°"
    elif not np.isfinite(gamma) or gamma <= 0:
        status = "缺少有效汞表面张力"
    else:
        status = ""

    if status:
        return MayerStoweResult(
            lo, hi, selected_volume, bulk_density, epsilon_raw, epsilon, ratio,
            selected_pressures, empty, empty, empty, status, result.sample_name,
        )

    particle_diameter = (
        MAYER_STOWE_DIAMETER_FACTOR_NM_PSIA_PER_DYNE_CM * gamma * ratio / selected_pressures
    )
    cumulative_percent = np.clip(
        100.0 * (selected_volumes - volume_low) / selected_volume,
        0.0,
        100.0,
    )
    incremental_percent = np.diff(cumulative_percent, prepend=cumulative_percent[0])
    return MayerStoweResult(
        lo,
        hi,
        selected_volume,
        bulk_density,
        epsilon_raw,
        epsilon,
        ratio,
        selected_pressures,
        np.asarray(particle_diameter, dtype=float),
        np.asarray(cumulative_percent, dtype=float),
        np.asarray(incremental_percent, dtype=float),
        "",
        result.sample_name,
    )
