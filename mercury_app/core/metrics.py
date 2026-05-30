from __future__ import annotations

import numpy as np

from .models import MercuryResult, SegmentMetrics


def metrics_for_pressure_range(
    result: MercuryResult,
    pressure_min: float,
    pressure_max: float,
    intrusion_only: bool = True,
) -> SegmentMetrics:
    """Return pore metrics for a selected pressure range."""
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

