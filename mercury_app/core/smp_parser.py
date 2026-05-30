from __future__ import annotations

from pathlib import Path

from smp_parser import (  # Reuse the currently validated reverse-engineered parser.
    AnalysisResult,
    MaterialInfo,
    MeasurementPoint,
    PenetrometerInfo,
    PressureStep,
    SMPFile,
    SMPParser,
    _washburn,
)


def parse_smp(filepath: str | Path) -> SMPFile:
    """Parse an SMP file and return the raw SMP structure."""
    return SMPParser().parse(str(filepath))


__all__ = [
    "AnalysisResult",
    "MaterialInfo",
    "MeasurementPoint",
    "PenetrometerInfo",
    "PressureStep",
    "SMPFile",
    "SMPParser",
    "_washburn",
    "parse_smp",
]

