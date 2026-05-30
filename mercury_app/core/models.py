from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np


PUBLIC_COLUMNS = (
    "pressure",
    "diameter",
    "cum_volume",
    "incremental_volume",
    "diff_intrusion_raw",
    "diff_intrusion_smooth",
    "log_diff_intrusion_raw",
    "log_diff_intrusion_smooth",
    "pct_total",
    "pct_incremental",
    "is_extrusion",
)


@dataclass
class MercuryResult:
    """Calculated, UI-safe result returned by load_smp()."""

    metadata: dict[str, Any]
    table: list[dict[str, float]]
    pressure: np.ndarray = field(repr=False)
    diameter: np.ndarray = field(repr=False)
    cum_volume: np.ndarray = field(repr=False)
    incremental_volume: np.ndarray = field(repr=False)
    diff_intrusion_raw: np.ndarray = field(repr=False)
    diff_intrusion: np.ndarray = field(repr=False)
    log_diff_intrusion_raw: np.ndarray = field(repr=False)
    log_diff_intrusion: np.ndarray = field(repr=False)
    pct_total: np.ndarray = field(repr=False)
    pct_incremental: np.ndarray = field(repr=False)
    is_extrusion: np.ndarray = field(repr=False)
    raw_smp: Any = field(default=None, repr=False)

    @classmethod
    def from_rows(
        cls,
        metadata: dict[str, Any],
        rows: list[dict[str, float]],
        raw_smp: Any = None,
    ) -> "MercuryResult":
        table = []
        for row in rows:
            table.append(
                {
                    "pressure": float(row["pressure"]),
                    "diameter": float(row["diameter"]),
                    "cum_volume": float(row["cum_vol"]),
                    "incremental_volume": float(row["incr_vol"]),
                    "diff_intrusion_raw": float(row.get("diff_intrusion_raw", 0.0)),
                    "diff_intrusion_smooth": float(row.get("diff_intrusion_smooth", 0.0)),
                    "log_diff_intrusion_raw": float(row.get("log_diff_intrusion_raw", 0.0)),
                    "log_diff_intrusion_smooth": float(row.get("log_diff_intrusion_smooth", 0.0)),
                    "pct_total": float(row.get("pct_total", 0.0)),
                    "pct_incremental": float(row.get("pct_incr", 0.0)),
                    "is_extrusion": float(row.get("is_extrusion", 0.0)),
                }
            )

        def column(name: str) -> np.ndarray:
            return np.asarray([row[name] for row in table], dtype=float)

        return cls(
            metadata=metadata,
            table=table,
            pressure=column("pressure"),
            diameter=column("diameter"),
            cum_volume=column("cum_volume"),
            incremental_volume=column("incremental_volume"),
            diff_intrusion_raw=column("diff_intrusion_raw"),
            diff_intrusion=column("diff_intrusion_smooth"),
            log_diff_intrusion_raw=column("log_diff_intrusion_raw"),
            log_diff_intrusion=column("log_diff_intrusion_smooth"),
            pct_total=column("pct_total"),
            pct_incremental=column("pct_incremental"),
            is_extrusion=column("is_extrusion"),
            raw_smp=raw_smp,
        )

    @property
    def data_point_count(self) -> int:
        return len(self.table)

    @property
    def max_pressure_index(self) -> int:
        if self.pressure.size == 0:
            return 0
        return int(np.nanargmax(self.pressure))

    @property
    def max_pressure(self) -> float:
        if self.pressure.size == 0:
            return 0.0
        return float(self.pressure[self.max_pressure_index])

    @property
    def total_pore_volume(self) -> float:
        if self.cum_volume.size == 0:
            return 0.0
        return float(self.cum_volume[self.max_pressure_index])

    @property
    def sample_name(self) -> str:
        value = (
            self.metadata.get("sample_name")
            or self.metadata.get("file_name")
            or self.metadata.get("material_name")
        )
        if value:
            return str(value)
        file_path = self.metadata.get("file_path")
        return Path(file_path).stem if file_path else ""


@dataclass(frozen=True)
class SegmentMetrics:
    pressure_min: float
    pressure_max: float
    diameter_min: float
    diameter_max: float
    pore_volume: float
    pore_volume_percent: float
    peak_diameter: float
    peak_value: float
    point_count: int

    def as_display_rows(self) -> list[tuple[str, str]]:
        return [
            ("Pressure range", _fmt_range(self.pressure_min, self.pressure_max, "psia")),
            ("Diameter range", _fmt_range(self.diameter_min, self.diameter_max, "nm")),
            ("Pore volume", _fmt(self.pore_volume, "mL/g")),
            ("Volume share", _fmt(self.pore_volume_percent, "%")),
            ("Peak diameter", _fmt(self.peak_diameter, "nm")),
            ("Peak dV/dlogD", _fmt(self.peak_value, "mL/g")),
            ("Points", str(self.point_count)),
        ]


def _fmt(value: float, suffix: str) -> str:
    if not np.isfinite(value):
        return ""
    return f"{value:.6g} {suffix}".strip()


def _fmt_range(value_min: float, value_max: float, suffix: str) -> str:
    if not np.isfinite(value_min) or not np.isfinite(value_max):
        return ""
    return f"{value_min:.6g} - {value_max:.6g} {suffix}".strip()
