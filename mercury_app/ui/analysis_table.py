"""Rows of the per-sample "分析结果" table shown beside the sample information."""
from __future__ import annotations

import math

from mercury_app.core import summary_metrics


ANALYSIS_HEADERS = (
    "样品",
    "进汞接触角 (°)", "表面张力 (dynes/cm)",
    "总入汞体积 (mL/g)",
    "总孔面积 (m²/g)",
    "中值孔径（体积） (nm)",
    "中值孔径（面积） (nm)",
    "平均孔径（4V/A） (nm)",
    "体积密度 (g/mL)",
    "表观骨架密度 (g/mL)",
    "孔隙率 (%)",
    "最大压力 (psia)",
    "压力范围 (psia)", "孔径范围 (nm)",
    "孔容 (mL/g)", "孔容占比 (%)",
    "峰值孔径 (nm)", "峰值 dV/dlogD (mL/g)",
)
# The rows from this header on follow the pressure range selected on the plot.
SELECTION_START = ANALYSIS_HEADERS.index("压力范围 (psia)")


def _number(value) -> tuple[float, str]:
    try:
        value = float(value)
    except (TypeError, ValueError):
        value = float("nan")
    return value, (f"{value:.6g}" if math.isfinite(value) else "—")


def _span(low, high) -> tuple[float, str]:
    (low, low_text), (_high, high_text) = _number(low), _number(high)
    if "—" in (low_text, high_text):
        return float("nan"), "—"
    return low, f"{low_text} – {high_text}"


def analysis_values(result, segment=None) -> list[tuple[float, str]]:
    """(sortable number, display text) for every header after "样品".

    ``segment`` is the ``SegmentMetrics`` of the selected pressure range; its
    rows show a dash until a range has been evaluated.
    """
    summary = summary_metrics(result)
    values = [_number(value) for value in (
        result.metadata.get("adv_contact_angle_deg"),
        result.metadata.get("surface_tension_dynes_cm"),
        summary.total_intrusion_volume,
        summary.total_pore_area,
        summary.median_volume_diameter,
        summary.median_area_diameter,
        summary.average_pore_diameter,
        summary.bulk_density,
        summary.apparent_density,
        summary.porosity,
        result.max_pressure,
    )]
    if segment is None:
        return values + [(float("nan"), "—")] * (len(ANALYSIS_HEADERS) - 1 - len(values))
    return values + [
        _span(segment.pressure_min, segment.pressure_max),
        _span(segment.diameter_min, segment.diameter_max),
        _number(segment.pore_volume), _number(segment.pore_volume_percent),
        _number(segment.peak_diameter), _number(segment.peak_value),
    ]
