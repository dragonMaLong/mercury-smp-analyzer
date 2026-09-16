from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.interpolate import Akima1DInterpolator
from scipy.optimize import minimize_scalar

from .metrics import summary_metrics
from .models import MercuryResult


NM2_TO_MDARCY = 1.0e-18 / 9.869233e-16


@dataclass(frozen=True)
class PoreStructureOptions:
    """Inputs used by the AutoPore-style pore-structure calculation."""

    surface_area_m2g: float | None = None
    use_calculated_surface_area: bool = True
    use_entered_density: bool = False
    bulk_density_gmL: float | None = None
    skeletal_density_gmL: float | None = None
    use_entered_conductivity_factor: bool = False
    conductivity_formation_factor: float = 0.025
    permeability_constant: float = 0.00442
    pore_shape_exponent: float = 1.0
    use_entered_threshold_pressure: bool = False
    threshold_pressure_psia: float | None = None


@dataclass(frozen=True)
class PoreStructureResult:
    sample_name: str
    pressure: np.ndarray
    cumulative_percent: np.ndarray
    permeability_md: float
    threshold_pressure_psia: float
    characteristic_length_nm: float
    conductivity_formation_factor: float
    tortuosity_factor: float
    tortuosity: float
    surface_area_m2g: float
    bulk_density_gmL: float
    skeletal_density_gmL: float
    threshold_is_calculated: bool
    conductivity_is_calculated: bool
    warning: str = ""

    @property
    def is_valid(self) -> bool:
        return bool(self.pressure.size) and np.isfinite(self.threshold_pressure_psia)

    def as_display_lines(self) -> list[str]:
        return [
            f"渗透率：{_fmt(self.permeability_md, 4)} mdarcy",
            f"阈值压力：{_fmt(self.threshold_pressure_psia, 2)} psia",
            f"特征长度：{_fmt(self.characteristic_length_nm, 2)} nm",
            f"传导形成因子：{_fmt(self.conductivity_formation_factor, 4)}",
            f"曲折因子：{_fmt(self.tortuosity_factor, 4)}",
            f"曲折度：{_fmt(self.tortuosity, 4)}",
        ]


def default_pore_structure_options(result: MercuryResult) -> PoreStructureOptions:
    summary = summary_metrics(result)
    bet_area = _number(result.metadata.get("bet_surface_area_m2g"))
    if not _positive(bet_area):
        bet_area = summary.total_pore_area
    bulk = _number(result.metadata.get("bulk_density_gmL"))
    if not _positive(bulk):
        bulk = summary.bulk_density
    skeletal = _number(result.metadata.get("true_density_gmL"))
    if not _positive(skeletal):
        skeletal = summary.apparent_density
    return PoreStructureOptions(
        surface_area_m2g=float(bet_area) if _positive(bet_area) else None,
        bulk_density_gmL=float(bulk) if _positive(bulk) else None,
        skeletal_density_gmL=float(skeletal) if _positive(skeletal) else None,
    )


def calculate_pore_structure(
    result: MercuryResult,
    options: PoreStructureOptions | None = None,
    pressure_min: float | None = None,
    pressure_max: float | None = None,
) -> PoreStructureResult:
    """Calculate the AutoPore-style Katz–Thompson pore-structure summary.

    The threshold is found from the maximum interior slope peak of an Akima
    interpolation of cumulative intrusion against pressure.  Permeability follows the
    Katz–Thompson procedure described in Micromeritics' AutoPore V calculation
    manual.  The result intentionally retains calculated/input provenance.
    """

    options = options or default_pore_structure_options(result)
    summary = summary_metrics(result)
    pressure, volume, diameter = _intrusion_data(result)
    if pressure.size < 3:
        return _empty_result(result.sample_name, "有效进汞数据点不足。")

    total_volume = float(volume[-1])
    curve_pressure, cumulative_percent = _selected_cumulative_percent(
        pressure, volume, pressure_min, pressure_max
    )
    washburn_constant = float(np.nanmedian(pressure * diameter))

    threshold_is_calculated = not (
        options.use_entered_threshold_pressure and _positive(options.threshold_pressure_psia)
    )
    if threshold_is_calculated:
        threshold_pressure = _calculated_threshold_pressure(
            pressure,
            volume,
            pressure_min=pressure_min,
            pressure_max=pressure_max,
        )
    else:
        threshold_pressure = float(options.threshold_pressure_psia)
    threshold_pressure = float(np.clip(threshold_pressure, pressure[0], pressure[-1]))
    characteristic_length = washburn_constant / threshold_pressure

    calculated_bulk = summary.bulk_density
    calculated_skeletal = summary.apparent_density
    bulk_density = (
        _number(options.bulk_density_gmL)
        if options.use_entered_density
        else calculated_bulk
    )
    skeletal_density = (
        _number(options.skeletal_density_gmL)
        if options.use_entered_density
        else calculated_skeletal
    )
    if not _positive(bulk_density):
        bulk_density = _number(options.bulk_density_gmL)
    if not _positive(skeletal_density):
        skeletal_density = _number(options.skeletal_density_gmL)

    permeability_constant = max(_number(options.permeability_constant, 0.00442), 1e-15)
    conductivity_is_calculated = not options.use_entered_conductivity_factor
    if options.use_entered_conductivity_factor:
        conductivity_factor = max(_number(options.conductivity_formation_factor), 0.0)
        permeability_nm2 = permeability_constant * characteristic_length**2 * conductivity_factor
    else:
        permeability_nm2 = _calculated_permeability_nm2(
            pressure,
            volume,
            diameter,
            threshold_pressure,
            characteristic_length,
            bulk_density,
        )
        conductivity_factor = (
            permeability_nm2 / (permeability_constant * characteristic_length**2)
            if characteristic_length > 0
            else float("nan")
        )
    permeability_md = permeability_nm2 * NM2_TO_MDARCY

    calculated_area = summary.total_pore_area
    surface_area = calculated_area if options.use_calculated_surface_area else _number(options.surface_area_m2g)
    if not _positive(surface_area):
        surface_area = calculated_area
    pore_shape_exponent = max(_number(options.pore_shape_exponent, 1.0), -0.99)
    tortuosity_factor = _tortuosity_factor(calculated_area, surface_area, pore_shape_exponent)
    tortuosity = _tortuosity(
        volume,
        diameter,
        permeability_nm2,
        bulk_density,
        skeletal_density,
        total_volume,
        characteristic_length,
    )

    warnings = []
    if not _positive(bulk_density):
        warnings.append("缺少有效体积密度")
    if not _positive(skeletal_density):
        warnings.append("缺少有效骨架密度")
    if not _positive(surface_area):
        warnings.append("缺少有效比表面积")
    return PoreStructureResult(
        sample_name=result.sample_name,
        pressure=curve_pressure,
        cumulative_percent=np.clip(cumulative_percent, 0.0, 100.0),
        permeability_md=float(permeability_md),
        threshold_pressure_psia=float(threshold_pressure),
        characteristic_length_nm=float(characteristic_length),
        conductivity_formation_factor=float(conductivity_factor),
        tortuosity_factor=float(tortuosity_factor),
        tortuosity=float(tortuosity),
        surface_area_m2g=float(surface_area),
        bulk_density_gmL=float(bulk_density),
        skeletal_density_gmL=float(skeletal_density),
        threshold_is_calculated=threshold_is_calculated,
        conductivity_is_calculated=conductivity_is_calculated,
        warning="；".join(warnings),
    )


def _selected_cumulative_percent(
    pressure: np.ndarray,
    volume: np.ndarray,
    pressure_min: float | None,
    pressure_max: float | None,
) -> tuple[np.ndarray, np.ndarray]:
    """Normalize only the selected intrusion increment, as in Mayer–Stowe.

    Insert interpolated boundary points so the actual selection starts at zero
    and ends at 100, even when its edges fall between measurement pressures.
    Keep these display arrays separate from the transport calculations.
    """
    empty = np.array([], dtype=float)
    if pressure.size == 0:
        return empty, empty
    lo = float(pressure[0] if pressure_min is None else pressure_min)
    hi = float(pressure[-1] if pressure_max is None else pressure_max)
    if not (np.isfinite(lo) and np.isfinite(hi)):
        return empty, empty
    lo, hi = sorted((lo, hi))
    lo = max(lo, float(pressure[0]))
    hi = min(hi, float(pressure[-1]))
    if lo >= hi:
        return empty, empty
    inner = pressure[(pressure > lo) & (pressure < hi)]
    selected_pressure = np.concatenate(([lo], inner, [hi]))
    selected_volume = np.interp(selected_pressure, pressure, volume)
    increment = float(selected_volume[-1] - selected_volume[0])
    if increment <= 0:
        return empty, empty
    percent = 100.0 * (selected_volume - selected_volume[0]) / increment
    return selected_pressure, np.clip(percent, 0.0, 100.0)


def _intrusion_data(result: MercuryResult) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    mask = (
        np.isfinite(result.pressure)
        & (result.pressure > 0)
        & np.isfinite(result.cum_volume)
        & np.isfinite(result.diameter)
        & (result.diameter > 0)
        & (result.is_extrusion < 0.5)
    )
    pressure = np.asarray(result.pressure[mask], dtype=float)
    volume = np.asarray(result.cum_volume[mask], dtype=float)
    diameter = np.asarray(result.diameter[mask], dtype=float)
    order = np.argsort(pressure)
    pressure, volume, diameter = pressure[order], volume[order], diameter[order]
    if pressure.size:
        unique = np.concatenate(([True], np.diff(pressure) > 0))
        pressure, volume, diameter = pressure[unique], volume[unique], diameter[unique]
    return pressure, volume, diameter


def _calculated_threshold_pressure(
    pressure: np.ndarray,
    volume: np.ndarray,
    pressure_min: float | None = None,
    pressure_max: float | None = None,
) -> float:
    if pressure_min is not None or pressure_max is not None:
        lo = float(pressure[0] if pressure_min is None else pressure_min)
        hi = float(pressure[-1] if pressure_max is None else pressure_max)
        lo, hi = sorted((lo, hi))
        selected = (pressure >= lo) & (pressure <= hi)
        if np.count_nonzero(selected) >= 3:
            pressure = pressure[selected]
            volume = volume[selected]
    if pressure.size < 5:
        slopes = np.gradient(volume, pressure)
        return float(pressure[int(np.nanargmax(slopes))])
    volume_spline = Akima1DInterpolator(pressure, volume)
    node_slopes = np.asarray(volume_spline.derivative()(pressure), dtype=float)
    slope_spline = Akima1DInterpolator(pressure, node_slopes)

    # AutoPore maximizes the second Akima spline at an interior stationary
    # point.  End points are selection boundaries rather than candidate
    # threshold peaks; treating them as candidates makes the result track the
    # left calculation bar incorrectly.
    stationary = np.asarray(slope_spline.derivative().roots(extrapolate=False), dtype=float)
    stationary = stationary[
        np.isfinite(stationary)
        & (stationary > pressure[0])
        & (stationary < pressure[-1])
    ]
    if stationary.size:
        values = np.asarray(slope_spline(stationary), dtype=float)
        finite = np.isfinite(values)
        if np.any(finite):
            candidates = stationary[finite]
            candidate_values = values[finite]
            return float(candidates[int(np.nanargmax(candidate_values))])

    interior = np.arange(1, pressure.size - 1)
    if interior.size:
        return float(pressure[int(interior[np.nanargmax(node_slopes[interior])])])
    return float(pressure[int(np.nanargmax(node_slopes))])


def _calculated_permeability_nm2(
    pressure: np.ndarray,
    volume: np.ndarray,
    diameter: np.ndarray,
    threshold_pressure: float,
    characteristic_length: float,
    bulk_density: float,
) -> float:
    if not (_positive(characteristic_length) and _positive(bulk_density)):
        return float("nan")
    log_pressure = np.log10(pressure)
    volume_spline = Akima1DInterpolator(log_pressure, volume)
    threshold_volume = float(volume_spline(np.log10(threshold_pressure)))
    connected_total = float(volume[-1] - threshold_volume)
    eligible = volume > threshold_volume
    if np.count_nonzero(eligible) < 2 or connected_total <= 0:
        return float("nan")

    x = np.asarray(diameter[eligible], dtype=float)
    conductance = np.asarray((volume[eligible] - threshold_volume) * x**3, dtype=float)
    order = np.argsort(x)
    x, conductance = x[order], conductance[order]
    unique = np.concatenate(([True], np.diff(x) > 0))
    x, conductance = x[unique], conductance[unique]
    if x.size >= 3:
        spline = Akima1DInterpolator(x, conductance)
        optimum = minimize_scalar(
            lambda value: -float(spline(value)),
            bounds=(float(x[0]), float(x[-1])),
            method="bounded",
        )
        maximum_length = float(optimum.x) if optimum.success else float(x[int(np.nanargmax(conductance))])
    else:
        maximum_length = float(x[int(np.nanargmax(conductance))])

    volume_at_maximum = float(np.interp(maximum_length, diameter[::-1], volume[::-1]))
    connected_fraction = float(np.clip((volume_at_maximum - threshold_volume) / connected_total, 0.0, 1.0))
    return float(
        (1.0 / 89.0)
        * maximum_length**2
        * (maximum_length / characteristic_length)
        * float(volume[-1])
        * float(bulk_density)
        * connected_fraction
    )


def _tortuosity_factor(calculated_area: float, bet_area: float, exponent: float) -> float:
    if not (_positive(calculated_area) and _positive(bet_area)):
        return float("nan")
    # Carniglia constriction correction.  At the cylindrical-pore default
    # epsilon=1 this reduces to sqrt(0.92*S_BET/S_MIP), matching AutoPore.
    return float((0.92 * bet_area / calculated_area) ** (1.0 / (1.0 + exponent)))


def _tortuosity(
    volume: np.ndarray,
    diameter: np.ndarray,
    permeability_nm2: float,
    bulk_density: float,
    skeletal_density: float,
    total_volume: float,
    characteristic_length: float,
) -> float:
    if not (
        _positive(permeability_nm2)
        and _positive(bulk_density)
        and _positive(skeletal_density)
        and _positive(characteristic_length)
    ):
        return float("nan")
    delta_volume = np.maximum(np.diff(volume), 0.0)
    mean_diameter = 0.5 * (diameter[:-1] + diameter[1:])
    # The transport moment is limited to the percolating pore family around
    # the characteristic length; larger entrance voids do not form part of
    # the Katz–Thompson backbone.
    transport = mean_diameter <= characteristic_length * np.sqrt(2.0)
    pore_moment = float(np.sum(delta_volume[transport] * mean_diameter[transport] ** 2))
    average_diameter_sq = skeletal_density * pore_moment
    solid_fraction = 1.0 - bulk_density * total_volume
    if average_diameter_sq <= 0 or solid_fraction <= 0:
        return float("nan")
    return float(np.sqrt(average_diameter_sq / (4.0 * 24.0 * permeability_nm2 * solid_fraction)))


def _empty_result(sample_name: str, warning: str) -> PoreStructureResult:
    return PoreStructureResult(
        sample_name=sample_name,
        pressure=np.array([], dtype=float),
        cumulative_percent=np.array([], dtype=float),
        permeability_md=float("nan"),
        threshold_pressure_psia=float("nan"),
        characteristic_length_nm=float("nan"),
        conductivity_formation_factor=float("nan"),
        tortuosity_factor=float("nan"),
        tortuosity=float("nan"),
        surface_area_m2g=float("nan"),
        bulk_density_gmL=float("nan"),
        skeletal_density_gmL=float("nan"),
        threshold_is_calculated=True,
        conductivity_is_calculated=True,
        warning=warning,
    )


def _number(value, default: float = float("nan")) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float(default)
    return number if np.isfinite(number) else float(default)


def _positive(value) -> bool:
    try:
        return bool(np.isfinite(float(value)) and float(value) > 0)
    except (TypeError, ValueError):
        return False


def _fmt(value: float, digits: int) -> str:
    if not np.isfinite(value):
        return "—"
    return f"{value:.{digits}f}"
