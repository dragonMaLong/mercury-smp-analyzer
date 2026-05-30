from __future__ import annotations

import os

os.environ.setdefault("PYQTGRAPH_QT_LIB", "PyQt5")

import numpy as np
import pyqtgraph as pg
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
) -> None:
    plot.clear()
    plot.setLogMode(x=False, y=False)
    all_x = []
    all_y = []
    for index, result in enumerate(results):
        if index >= len(visible) or not visible[index]:
            continue
        color = colors[index % len(colors)]
        mask = (
            np.isfinite(result.diameter)
            & (result.diameter > 0)
            & np.isfinite(result.log_diff_intrusion)
            & (result.is_extrusion < 0.5)
        )
        x_values = result.diameter[mask]
        y_values = np.maximum(result.log_diff_intrusion[mask], 0.0)
        order = np.argsort(x_values)
        plot.plot(
            x_values[order],
            y_values[order],
            pen=pg.mkPen(color, width=2),
            symbol="o",
            symbolSize=5,
            symbolPen=pg.mkPen(color, width=1),
            symbolBrush=pg.mkBrush("#ffffff"),
            name=_legend_name(result),
        )
        if x_values.size:
            all_x.append(x_values)
            all_y.append(y_values)

    plot.setLabel("bottom", "Pore Diameter (nm)")
    plot.setLogMode(x=True, y=False)
    x_values = np.concatenate(all_x) if all_x else np.array([])
    y_values = np.concatenate(all_y) if all_y else np.array([])
    if x_values.size:
        plot.setXRange(float(np.log10(np.nanmin(x_values))), float(np.log10(np.nanmax(x_values))), padding=0.03)
        plot.setYRange(float(np.nanmin(y_values)), float(np.nanmax(y_values)), padding=0.08)


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
