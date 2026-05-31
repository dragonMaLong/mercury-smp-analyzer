from __future__ import annotations

import os

os.environ.setdefault("PYQTGRAPH_QT_LIB", "PyQt5")

import numpy as np
import pyqtgraph as pg
from scipy.interpolate import Akima1DInterpolator
from pyqtgraph.Qt import QtCore


pg.setConfigOptions(antialias=True, useOpenGL=False)


DEFAULT_COLORS = (
    "#2563eb",
    "#dc2626",
    "#16a34a",
    "#9333ea",
    "#f97316",
    "#0891b2",
    "#4f46e5",
    "#be123c",
    "#65a30d",
    "#b45309",
    "#0f766e",
    "#db2777",
    "#475569",
    "#7c3aed",
    "#0284c7",
    "#ca8a04",
    "#15803d",
    "#c026d3",
    "#991b1b",
    "#334155",
)

DISTRIBUTION_CURVE_POINT_COUNT = 360


class PlainNumberAxis(pg.AxisItem):
    def tickStrings(self, values, scale, spacing):
        max_labels = self._max_label_count()
        step = max(1, int(np.ceil(len(values) / max_labels))) if len(values) else 1
        labels = []
        for index, value in enumerate(values):
            if index % step:
                labels.append("")
                continue
            axis_value = float(value) * scale
            if getattr(self, "logMode", False):
                axis_value = 10.0**axis_value
            labels.append(_plain_number(axis_value))
        return labels

    def _max_label_count(self) -> int:
        width = max(1.0, float(self.geometry().width()))
        return max(3, int(width // 95))


def make_plot(title: str, left_label: str, bottom_label: str) -> pg.PlotWidget:
    axis = PlainNumberAxis(orientation="bottom")
    axis.setStyle(tickTextWidth=86, autoExpandTextSpace=True)
    plot = pg.PlotWidget(axisItems={"bottom": axis})
    plot.setBackground("w")
    plot.showGrid(x=True, y=True, alpha=0.25)
    plot.setTitle(title)
    plot.setLabel("left", left_label)
    plot.setLabel("bottom", bottom_label)
    plot.setMenuEnabled(True)
    plot.addLegend(
        offset=(10, 10),
        labelTextColor="#111827",
        brush=pg.mkBrush(255, 255, 255, 210),
        pen=pg.mkPen("#d1d5db"),
    )
    return plot


def plot_pressure_volume(plot: pg.PlotWidget, result) -> None:
    plot_pressure_volume_multi(plot, [result], [True], [DEFAULT_COLORS[0]])


def plot_pressure_volume_multi(
    plot: pg.PlotWidget,
    results,
    visible: list[bool],
    colors: list[str],
) -> None:
    plot.clear()
    plot.setLogMode(x=True, y=False)
    all_x = []
    all_y = []
    for index, result in enumerate(results):
        if index >= len(visible) or not visible[index]:
            continue
        color = colors[index % len(colors)]
        finite = np.isfinite(result.pressure) & np.isfinite(result.cum_volume)
        intrusion = finite & (result.is_extrusion < 0.5)
        extrusion = finite & (result.is_extrusion >= 0.5)
        label = _legend_name(result)

        _plot_cycle(plot, result, intrusion, color, label, symbol_brush=color)
        _plot_cycle(plot, result, extrusion, color, None, style=QtDashLine(), symbol_brush="#ffffff")

        if np.any(finite):
            all_x.append(result.pressure[finite])
            all_y.append(result.cum_volume[finite])

    x_values = np.concatenate(all_x) if all_x else np.array([])
    y_values = np.concatenate(all_y) if all_y else np.array([])
    if x_values.size:
        plot.setXRange(float(np.log10(np.nanmin(x_values))), float(np.log10(np.nanmax(x_values))), padding=0.03)
        plot.setYRange(float(np.nanmin(y_values)), float(np.nanmax(y_values)), padding=0.08)


def plot_distribution(plot: pg.PlotWidget, result) -> None:
    plot_distribution_multi(plot, [result], [True], [DEFAULT_COLORS[2]])


def plot_distribution_multi(
    plot: pg.PlotWidget,
    results,
    visible: list[bool],
    colors: list[str],
) -> list[dict[str, np.ndarray] | None]:
    plot.clear()
    plot.setLogMode(x=False, y=False)
    all_x = []
    all_y = []
    curve_data_by_index: list[dict[str, np.ndarray] | None] = [None] * len(results)
    for index, result in enumerate(results):
        if index >= len(visible) or not visible[index]:
            continue
        color = colors[index % len(colors)]
        data = distribution_plot_data(result)
        curve_data_by_index[index] = data
        curve_item = plot.plot(
            data["curve_x"],
            data["curve_y"],
            pen=pg.mkPen(color, width=2),
            name=_legend_name(result),
        )
        point_item = plot.plot(
            data["x"],
            data["y"],
            pen=None,
            symbol="o",
            symbolSize=5,
            symbolPen=pg.mkPen(color, width=1),
            symbolBrush=pg.mkBrush("#ffffff"),
        )
        _link_visibility(curve_item, point_item)
        if data["x"].size:
            all_x.append(data["x"])
            all_y.append(data["y"])

    plot.setLabel("bottom", "Pore Diameter (nm)")
    plot.setLogMode(x=True, y=False)
    x_values = np.concatenate(all_x) if all_x else np.array([])
    y_values = np.concatenate(all_y) if all_y else np.array([])
    if x_values.size:
        plot.setXRange(float(np.log10(np.nanmin(x_values))), float(np.log10(np.nanmax(x_values))), padding=0.03)
        plot.setYRange(float(np.nanmin(y_values)), float(np.nanmax(y_values)), padding=0.08)
    return curve_data_by_index


def _link_visibility(primary_item, *linked_items) -> None:
    original_set_visible = primary_item.setVisible

    def set_visible(visible: bool) -> None:
        original_set_visible(visible)
        for item in linked_items:
            item.setVisible(visible)

    primary_item.setVisible = set_visible


def distribution_plot_data(result) -> dict[str, np.ndarray]:
    mask = (
        np.isfinite(result.diameter)
        & (result.diameter > 0)
        & np.isfinite(result.log_diff_intrusion)
        & (result.is_extrusion < 0.5)
    )
    x_values = result.diameter[mask]
    y_values = np.maximum(result.log_diff_intrusion[mask], 0.0)
    order = np.argsort(x_values)
    ordered_x = x_values[order]
    ordered_y = y_values[order]
    curve_x, curve_y = smooth_log_distribution_curve(ordered_x, ordered_y)
    return {
        "x": ordered_x,
        "y": ordered_y,
        "curve_x": curve_x,
        "curve_y": curve_y,
    }


def clip_log_curve_to_range(
    curve_x: np.ndarray,
    curve_y: np.ndarray,
    x_min: float,
    x_max: float,
) -> tuple[np.ndarray, np.ndarray]:
    mask = np.isfinite(curve_x) & (curve_x > 0) & np.isfinite(curve_y)
    curve_x = np.asarray(curve_x[mask], dtype=float)
    curve_y = np.asarray(curve_y[mask], dtype=float)
    if curve_x.size == 0:
        return np.array([]), np.array([])

    order = np.argsort(curve_x)
    curve_x = curve_x[order]
    curve_y = curve_y[order]
    lo, hi = sorted((float(x_min), float(x_max)))
    lo = max(lo, float(curve_x[0]))
    hi = min(hi, float(curve_x[-1]))
    if not (np.isfinite(lo) and np.isfinite(hi)) or lo > hi:
        return np.array([]), np.array([])

    if curve_x.size == 1 or lo == hi:
        return np.array([lo]), np.array([float(np.interp(lo, curve_x, curve_y))])

    log_curve_x = np.log10(curve_x)

    def interpolate_y(x_value: float) -> float:
        return float(np.interp(np.log10(x_value), log_curve_x, curve_y))

    inner_mask = (curve_x > lo) & (curve_x < hi)
    selected_x = [lo]
    selected_y = [interpolate_y(lo)]
    selected_x.extend(curve_x[inner_mask].tolist())
    selected_y.extend(curve_y[inner_mask].tolist())
    selected_x.append(hi)
    selected_y.append(interpolate_y(hi))
    return np.asarray(selected_x, dtype=float), np.asarray(selected_y, dtype=float)


def _plot_cycle(
    plot: pg.PlotWidget,
    result,
    mask: np.ndarray,
    color: str,
    name: str | None,
    style=None,
    symbol_brush=None,
) -> None:
    x_values = result.pressure[mask]
    y_values = result.cum_volume[mask]
    if not x_values.size:
        return
    pen = pg.mkPen(color, width=2)
    if style is not None:
        pen.setStyle(style)
    plot.plot(
        x_values,
        y_values,
        pen=pen,
        symbol="o",
        symbolSize=5,
        symbolPen=pg.mkPen(color, width=1),
        symbolBrush=pg.mkBrush(symbol_brush or color),
        name=name,
    )


def QtDashLine():
    return QtCore.Qt.DashLine


def _legend_name(result) -> str:
    return str(result.metadata.get("file_name") or result.sample_name or "Sample")


def smooth_log_distribution_curve(x_values: np.ndarray, y_values: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    mask = np.isfinite(x_values) & (x_values > 0) & np.isfinite(y_values)
    x_values = np.asarray(x_values[mask], dtype=float)
    y_values = np.asarray(y_values[mask], dtype=float)
    if x_values.size < 3:
        return x_values, y_values

    log_x = np.log10(x_values)
    order = np.argsort(log_x)
    log_x = log_x[order]
    y_values = y_values[order]

    unique_log_x, inverse = np.unique(log_x, return_inverse=True)
    if unique_log_x.size < 3 or unique_log_x[0] == unique_log_x[-1]:
        return 10.0**unique_log_x, y_values[: unique_log_x.size]

    unique_y = np.zeros_like(unique_log_x)
    counts = np.zeros_like(unique_log_x)
    np.add.at(unique_y, inverse, y_values)
    np.add.at(counts, inverse, 1.0)
    unique_y = unique_y / np.maximum(counts, 1.0)

    grid_size = max(DISTRIBUTION_CURVE_POINT_COUNT, unique_log_x.size * 8)
    grid_x = np.linspace(unique_log_x[0], unique_log_x[-1], grid_size)
    try:
        interpolator = Akima1DInterpolator(unique_log_x, unique_y, method="akima")
        grid_y = np.asarray(interpolator(grid_x), dtype=float)
    except (TypeError, ValueError):
        grid_y = np.interp(grid_x, unique_log_x, unique_y)

    grid_y = np.nan_to_num(grid_y, nan=0.0, posinf=0.0, neginf=0.0)
    return 10.0**grid_x, np.maximum(grid_y, 0.0)


def _plain_number(value: float) -> str:
    if not np.isfinite(value):
        return ""
    abs_value = abs(value)
    if abs_value >= 100:
        return f"{value:,.0f}"
    if abs_value >= 10:
        return f"{value:,.1f}".rstrip("0").rstrip(".")
    if abs_value >= 1:
        return f"{value:,.2f}".rstrip("0").rstrip(".")
    if abs_value >= 0.01:
        return f"{value:.3f}".rstrip("0").rstrip(".")
    if abs_value == 0:
        return "0"
    return f"{value:.6f}".rstrip("0").rstrip(".")
