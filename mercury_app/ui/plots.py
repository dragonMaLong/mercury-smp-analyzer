from __future__ import annotations

import os
import math

os.environ.setdefault("PYQTGRAPH_QT_LIB", "PyQt5")

import numpy as np
import pyqtgraph as pg
from scipy.interpolate import Akima1DInterpolator
from pyqtgraph.Qt import QtCore, QtGui, QtWidgets


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


def _legend_contains_scene_pos(plot, scene_pos: QtCore.QPointF) -> bool:
    legend = getattr(plot.plotItem, "legend", None)
    if legend is None or not legend.isVisible():
        return False
    try:
        return bool(legend.sceneBoundingRect().contains(scene_pos))
    except Exception:
        return False


def _legend_sample_at_scene_pos(plot, scene_pos: QtCore.QPointF) -> int | None:
    if not _legend_contains_scene_pos(plot, scene_pos):
        return None
    for entry in getattr(plot, "_sample_legend_graphics_entries", []):
        sample_index = entry.get("sample_index")
        for key in ("sample_item", "label_item"):
            item = entry.get(key)
            if item is None:
                continue
            try:
                if item.sceneBoundingRect().adjusted(-3, -3, 3, 3).contains(scene_pos):
                    return int(sample_index)
            except Exception:
                continue
    return None


def _refresh_legend_layout(plot) -> None:
    legend = getattr(plot.plotItem, "legend", None)
    if legend is None:
        return
    for method_name in ("updateSize", "adjustSize", "updateGeometry"):
        method = getattr(legend, method_name, None)
        if callable(method):
            try:
                method()
            except Exception:
                pass


def _set_legend_sample_hover(plot, sample_index: int | None) -> None:
    normalized_index = int(sample_index) if sample_index is not None else None
    current_index = getattr(plot, "_sample_legend_hover_index", None)
    if current_index == normalized_index:
        return
    setattr(plot, "_sample_legend_hover_index", normalized_index)
    changed = False
    for entry in getattr(plot, "_sample_legend_graphics_entries", []):
        label_item = entry.get("label_item")
        if label_item is None:
            continue
        try:
            base_font = entry.get("base_font")
            if not isinstance(base_font, QtGui.QFont):
                item = getattr(label_item, "item", None)
                base_font = item.font() if item is not None and hasattr(item, "font") else label_item.font()
                entry["base_font"] = QtGui.QFont(base_font)
            font = QtGui.QFont(base_font)
            font.setBold(normalized_index is not None and int(entry.get("sample_index", -1)) == normalized_index)
            if hasattr(label_item, "setFont"):
                label_item.setFont(font)
                changed = True
            item = getattr(label_item, "item", None)
            if item is not None and hasattr(item, "setFont"):
                item.setFont(font)
                changed = True
        except Exception:
            pass
    if changed:
        _refresh_legend_layout(plot)


class _LegendToggleButton(QtWidgets.QToolButton):
    def __init__(self, plot: pg.PlotWidget) -> None:
        super().__init__(plot)
        self._plot = plot
        self.setCheckable(True)
        self.setChecked(True)
        self.setAutoRaise(True)
        self.setFixedSize(24, 22)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setToolTip("隐藏图例")
        self._press_global_pos: QtCore.QPoint | None = None
        self._press_button_pos: QtCore.QPoint | None = None
        self._dragging_button = False
        self.toggled.connect(self._on_toggled)

    def _on_toggled(self, checked: bool) -> None:
        _set_plot_legend_visible(self._plot, checked)

    def mousePressEvent(self, event) -> None:
        if event.button() != QtCore.Qt.LeftButton:
            super().mousePressEvent(event)
            return
        self._press_global_pos = self._event_global_pos(event)
        self._press_button_pos = QtCore.QPoint(self.pos())
        self._dragging_button = False
        event.accept()

    def mouseMoveEvent(self, event) -> None:
        if not (
            event.buttons() & QtCore.Qt.LeftButton
            and self._press_global_pos is not None
            and self._press_button_pos is not None
        ):
            super().mouseMoveEvent(event)
            return
        delta = self._event_global_pos(event) - self._press_global_pos
        if not self._dragging_button and delta.manhattanLength() < QtWidgets.QApplication.startDragDistance():
            event.accept()
            return
        self._dragging_button = True
        self._move_to(self._press_button_pos + delta)
        event.accept()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() != QtCore.Qt.LeftButton:
            super().mouseReleaseEvent(event)
            return
        if self._dragging_button:
            self._dragging_button = False
            self._press_global_pos = None
            self._press_button_pos = None
            event.accept()
            return
        self._press_global_pos = None
        self._press_button_pos = None
        self.setChecked(not self.isChecked())
        event.accept()

    def _move_to(self, position: QtCore.QPoint) -> None:
        margin = 4
        x = max(margin, min(int(position.x()), max(margin, self._plot.width() - self.width() - margin)))
        y = max(margin, min(int(position.y()), max(margin, self._plot.height() - self.height() - margin)))
        self.move(x, y)
        self.raise_()
        legend = getattr(self._plot.plotItem, "legend", None)
        if legend is not None and legend.isVisible():
            _move_legend_to_toggle_anchor(self._plot)
        else:
            setattr(self._plot, "_legend_hidden_toggle_anchor", QtCore.QPoint(self.pos()))

    @staticmethod
    def _event_global_pos(event) -> QtCore.QPoint:
        if hasattr(event, "globalPosition"):
            return event.globalPosition().toPoint()
        return event.globalPos()

    def paintEvent(self, event) -> None:
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        rect = self.rect().adjusted(1, 1, -1, -1)
        hovered = bool(self.underMouse())
        painter.setPen(QtGui.QPen(QtGui.QColor("#cbd5e1"), 1))
        painter.setBrush(QtGui.QBrush(QtGui.QColor("#ffffff" if not hovered else "#f8fafc")))
        painter.drawRoundedRect(rect, 5, 5)

        icon_rect = QtCore.QRectF(7, 7, 14, 10)
        painter.setPen(QtGui.QPen(QtGui.QColor("#334155"), 1.6))
        painter.setBrush(QtCore.Qt.NoBrush)
        path = QtGui.QPainterPath()
        path.moveTo(icon_rect.left(), icon_rect.center().y())
        path.cubicTo(
            icon_rect.left() + 3,
            icon_rect.top(),
            icon_rect.right() - 3,
            icon_rect.top(),
            icon_rect.right(),
            icon_rect.center().y(),
        )
        path.cubicTo(
            icon_rect.right() - 3,
            icon_rect.bottom(),
            icon_rect.left() + 3,
            icon_rect.bottom(),
            icon_rect.left(),
            icon_rect.center().y(),
        )
        painter.drawPath(path)
        if self.isChecked():
            painter.setBrush(QtGui.QBrush(QtGui.QColor("#334155")))
            painter.drawEllipse(QtCore.QPointF(icon_rect.center()), 2.3, 2.3)
        else:
            painter.drawLine(QtCore.QLineF(7, 18, 21, 6))


class _LegendToggleEventFilter(QtCore.QObject):
    def eventFilter(self, obj, event) -> bool:
        plot = self.parent()
        if event.type() in {
            QtCore.QEvent.Resize,
            QtCore.QEvent.Show,
            QtCore.QEvent.MouseMove,
            QtCore.QEvent.MouseButtonRelease,
        }:
            _position_legend_toggle_button(plot)
        return False


def _install_legend_toggle(plot: pg.PlotWidget) -> None:
    if getattr(plot, "_legend_toggle_button", None) is not None:
        return
    setattr(plot, "_legend_visible", True)
    original_clear = plot.clear

    def clear_with_legend_toggle(*args, **kwargs):
        result = original_clear(*args, **kwargs)
        _apply_plot_legend_visibility(plot)
        button = getattr(plot, "_legend_toggle_button", None)
        if button is not None:
            button.show()
            button.raise_()
        return result

    plot.clear = clear_with_legend_toggle
    button = _LegendToggleButton(plot)
    event_filter = _LegendToggleEventFilter(plot)
    plot.installEventFilter(event_filter)
    viewport = getattr(plot, "viewport", lambda: None)()
    if viewport is not None:
        viewport.installEventFilter(event_filter)
    setattr(plot, "_legend_toggle_button", button)
    setattr(plot, "_legend_toggle_event_filter", event_filter)
    _position_legend_toggle_button(plot)
    button.show()
    button.raise_()


def _apply_default_legend_position(plot: pg.PlotWidget) -> None:
    if getattr(plot, "_legend_user_offset", None) is not None:
        return
    if getattr(plot, "_legend_default_position", "left") != "right":
        return
    legend = getattr(plot.plotItem, "legend", None)
    if legend is None:
        return
    try:
        _refresh_legend_layout(plot)
        rect = plot.plotItem.vb.sceneBoundingRect()
        legend_rect = legend.sceneBoundingRect()
        legend.anchor(
            itemPos=(1, 0),
            parentPos=(1, 0),
            offset=(
                -max(10.0, float(rect.width() - legend_rect.width()) * 0.02),
                10.0,
            ),
        )
    except Exception:
        legend.anchor(itemPos=(1, 0), parentPos=(1, 0), offset=(-10, 10))


def _position_legend_toggle_button(plot: pg.PlotWidget) -> None:
    button = getattr(plot, "_legend_toggle_button", None)
    if button is None:
        return
    _refresh_legend_layout(plot)
    margin = 4
    position = _legend_toggle_position(plot, button)
    x = max(margin, min(int(position.x()), max(margin, plot.width() - button.width() - margin)))
    y = max(margin, min(int(position.y()), max(margin, plot.height() - button.height() - margin)))
    button.move(x, y)
    button.raise_()


def _legend_toggle_position(plot: pg.PlotWidget, button: QtWidgets.QToolButton) -> QtCore.QPoint:
    legend = getattr(plot.plotItem, "legend", None)
    if legend is not None and legend.isVisible():
        try:
            rect = _legend_rect_in_plot(plot)
            if rect is None:
                raise RuntimeError("legend rect unavailable")
            return QtCore.QPoint(int(rect.right() - button.width() - 4), int(rect.top() + 4))
        except Exception:
            pass
    anchor = getattr(plot, "_legend_hidden_toggle_anchor", None)
    if isinstance(anchor, QtCore.QPoint):
        return QtCore.QPoint(anchor)
    return QtCore.QPoint(max(4, plot.width() - button.width() - 8), 8)


def _move_legend_to_toggle_anchor(plot: pg.PlotWidget) -> None:
    legend = getattr(plot.plotItem, "legend", None)
    button = getattr(plot, "_legend_toggle_button", None)
    if legend is None or button is None or not legend.isVisible():
        return
    rect = _legend_rect_in_plot(plot)
    if rect is None:
        return
    try:
        legend_pos = legend.pos()
        base_x = rect.left() - float(legend_pos.x())
        base_y = rect.top() - float(legend_pos.y())
        desired_rect_left = button.x() + button.width() + 4 - rect.width()
        desired_rect_top = button.y() - 4
        desired_offset = (
            float(desired_rect_left) - base_x,
            float(desired_rect_top) - base_y,
        )
        _apply_legend_offset(plot, desired_offset)
        setattr(plot, "_legend_user_offset", (float(desired_offset[0]), float(desired_offset[1])))
    except Exception:
        return
    _position_legend_toggle_button(plot)


def _apply_legend_offset(plot: pg.PlotWidget, offset: tuple[float, float]) -> None:
    legend = getattr(plot.plotItem, "legend", None)
    if legend is None:
        return
    legend.anchor(itemPos=(0, 0), parentPos=(0, 0), offset=(float(offset[0]), float(offset[1])))


def _legend_rect_in_plot(plot: pg.PlotWidget) -> QtCore.QRect | None:
    legend = getattr(plot.plotItem, "legend", None)
    if legend is None:
        return None
    try:
        _refresh_legend_layout(plot)
        scene_rect = legend.sceneBoundingRect()
        top_left = plot.mapFromScene(scene_rect.topLeft())
        bottom_right = plot.mapFromScene(scene_rect.bottomRight())
        return QtCore.QRect(top_left, bottom_right).normalized()
    except Exception:
        return None


def _set_plot_legend_visible(plot: pg.PlotWidget, visible: bool) -> None:
    setattr(plot, "_legend_visible", bool(visible))
    _apply_plot_legend_visibility(plot)


def _apply_plot_legend_visibility(plot: pg.PlotWidget) -> None:
    visible = bool(getattr(plot, "_legend_visible", True))
    legend = getattr(plot.plotItem, "legend", None)
    button = getattr(plot, "_legend_toggle_button", None)
    if legend is not None and button is not None and not visible:
        setattr(plot, "_legend_hidden_toggle_anchor", QtCore.QPoint(button.pos()))
    if legend is not None:
        legend.setVisible(bool(visible))
        if visible:
            hidden_anchor = getattr(plot, "_legend_hidden_toggle_anchor", None)
            if isinstance(hidden_anchor, QtCore.QPoint):
                _move_legend_to_toggle_anchor(plot)
                try:
                    delattr(plot, "_legend_hidden_toggle_anchor")
                except AttributeError:
                    pass
            else:
                user_offset = getattr(plot, "_legend_user_offset", None)
                if user_offset is not None:
                    _apply_legend_offset(plot, user_offset)
                else:
                    _apply_default_legend_position(plot)
    if button is not None:
        button.blockSignals(True)
        button.setChecked(bool(visible))
        button.setToolTip("隐藏图例" if visible else "显示图例")
        button.blockSignals(False)
        _position_legend_toggle_button(plot)
        button.update()


def _sync_plot_legend_visibility(plot: pg.PlotWidget) -> None:
    _apply_plot_legend_visibility(plot)
    QtCore.QTimer.singleShot(0, lambda plot=plot: _finalize_legend_layout(plot))


def _finalize_legend_layout(plot: pg.PlotWidget) -> None:
    _apply_default_legend_position(plot)
    _position_legend_toggle_button(plot)


class ClickProjectionCursor:
    def __init__(self, plot: pg.PlotWidget) -> None:
        self.plot = plot
        self.plot_item = plot.getPlotItem()
        self.view_box = self.plot_item.getViewBox()
        self.point: tuple[float, float] | None = None
        pen = pg.mkPen("#2563eb", width=1, style=QtCore.Qt.DashLine)
        self.vertical_line = pg.PlotCurveItem(pen=pen)
        self.horizontal_line = pg.PlotCurveItem(pen=pen)
        self.x_label = pg.TextItem(
            text="",
            color="#111827",
            anchor=(0.5, 1.0),
            fill=pg.mkBrush(255, 255, 255, 235),
            border=pg.mkPen("#2563eb"),
        )
        self.y_label = pg.TextItem(
            text="",
            color="#111827",
            anchor=(0.0, 0.5),
            fill=pg.mkBrush(255, 255, 255, 235),
            border=pg.mkPen("#2563eb"),
        )
        for item in (self.vertical_line, self.horizontal_line, self.x_label, self.y_label):
            item.setZValue(10_000)
            self.view_box.addItem(item, ignoreBounds=True)
            item.hide()
        self.plot.scene().sigMouseClicked.connect(self._on_mouse_clicked)
        self.view_box.sigRangeChanged.connect(lambda *_args: self.update())

    def reattach(self) -> None:
        added_items = getattr(self.view_box, "addedItems", [])
        for item in (self.vertical_line, self.horizontal_line, self.x_label, self.y_label):
            if item not in added_items:
                self.view_box.addItem(item, ignoreBounds=True)
        self.update()

    def _on_mouse_clicked(self, event) -> None:
        if event.button() != QtCore.Qt.LeftButton:
            return
        if _legend_contains_scene_pos(self.plot, event.scenePos()):
            if hasattr(event, "accept"):
                event.accept()
            return
        ignore_callback = getattr(self.plot, "_click_projection_ignore_callback", None)
        if callable(ignore_callback) and ignore_callback(event.scenePos()):
            if hasattr(event, "accept"):
                event.accept()
            return
        double_click = getattr(event, "double", False)
        is_double_click = double_click() if callable(double_click) else bool(double_click)
        if is_double_click:
            self.clear()
            return
        self.set_scene_position(event.scenePos())

    def set_scene_position(self, scene_pos: QtCore.QPointF) -> bool:
        if not self.view_box.sceneBoundingRect().contains(scene_pos):
            return False
        view_pos = self.view_box.mapSceneToView(scene_pos)
        x = float(view_pos.x())
        y = float(view_pos.y())
        if not np.isfinite(x) or not np.isfinite(y):
            return False
        self.point = (x, y)
        self.update()
        return True

    def clear(self) -> None:
        self.point = None
        self.hide()

    def update(self) -> None:
        if self.point is None:
            self.hide()
            return
        x, y = self.point
        view_range = self.view_box.viewRange()
        if not view_range or len(view_range) != 2:
            self.hide()
            return
        (x_min, x_max), (y_min, y_max) = view_range
        if not all(np.isfinite(value) for value in (x_min, x_max, y_min, y_max)):
            self.hide()
            return
        if (
            x < min(x_min, x_max)
            or x > max(x_min, x_max)
            or y < min(y_min, y_max)
            or y > max(y_min, y_max)
        ):
            self.hide()
            return

        left, right = min(x_min, x_max), max(x_min, x_max)
        bottom, top = min(y_min, y_max), max(y_min, y_max)
        self.vertical_line.setData([x, x], [bottom, top])
        self.horizontal_line.setData([left, right], [y, y])
        self.x_label.setText(self._label_text("bottom", x))
        self.y_label.setText(self._label_text("left", y))
        self.x_label.setPos(x, bottom)
        self.y_label.setPos(left, y)
        for item in (self.vertical_line, self.horizontal_line, self.x_label, self.y_label):
            item.show()

    def hide(self) -> None:
        for item in (self.vertical_line, self.horizontal_line, self.x_label, self.y_label):
            item.hide()

    def _label_text(self, axis_name: str, coordinate: float) -> str:
        axis = self.plot_item.getAxis(axis_name)
        value = coordinate
        if getattr(axis, "logMode", False):
            try:
                value = 10.0**coordinate
            except OverflowError:
                return ""
        return _plain_number(float(value))


def _enable_click_projection_cursor(plot: pg.PlotWidget) -> None:
    cursor = ClickProjectionCursor(plot)
    original_clear = plot.clear

    def clear_with_cursor(*args, **kwargs):
        result = original_clear(*args, **kwargs)
        cursor.point = None
        cursor.reattach()
        return result

    plot.clear = clear_with_cursor
    plot._click_projection_cursor = cursor


class SampleCurveInteractionController(QtCore.QObject):
    HOVER_DELAY_MS = 0
    HIT_DISTANCE_PX = 12.0

    def __init__(self, plot: pg.PlotWidget) -> None:
        super().__init__(plot)
        self.plot = plot
        self.plot_item = plot.getPlotItem()
        self.view_box = self.plot_item.getViewBox()
        self.entries: list[dict[str, object]] = []
        self.hovered_entry: dict[str, object] | None = None
        self.hovered_sample_index: int | None = None
        self.selected_sample_index: int | None = None
        self.pending_scene_pos: QtCore.QPointF | None = None
        self.hover_timer = QtCore.QTimer(self)
        self.hover_timer.setSingleShot(True)
        self.hover_timer.setInterval(self.HOVER_DELAY_MS)
        self.hover_timer.timeout.connect(self._resolve_hover)
        self.tooltip = self._create_tooltip()
        self.plot.scene().sigMouseMoved.connect(self._on_mouse_moved)
        self.plot.scene().sigMouseClicked.connect(self._on_mouse_clicked)
        self.plot.installEventFilter(self)
        viewport = getattr(self.plot, "viewport", lambda: None)()
        if viewport is not None:
            viewport.installEventFilter(self)

    def _create_tooltip(self):
        tooltip = pg.TextItem(
            text="",
            color="#111827",
            anchor=(0.0, 1.0),
            fill=pg.mkBrush(255, 255, 255, 238),
            border=pg.mkPen("#2563eb"),
        )
        tooltip.setZValue(20_000)
        tooltip.hide()
        return tooltip

    def _ensure_tooltip(self):
        try:
            self.tooltip.isVisible()
        except RuntimeError:
            self.tooltip = self._create_tooltip()
        return self.tooltip

    def _hide_tooltip(self) -> None:
        try:
            self.tooltip.hide()
        except RuntimeError:
            pass

    def _tooltip_is_visible(self) -> bool:
        try:
            return bool(self.tooltip.isVisible())
        except RuntimeError:
            return False

    def eventFilter(self, obj, event) -> bool:
        if event.type() in {QtCore.QEvent.Leave, QtCore.QEvent.Hide}:
            self.clear_hover()
        return False

    def reset(self) -> None:
        self.hover_timer.stop()
        self.entries = []
        self.pending_scene_pos = None
        self.hovered_entry = None
        self.hovered_sample_index = None
        self.selected_sample_index = None
        self._hide_tooltip()
        _set_legend_sample_hover(self.plot, None)

    def register(
        self,
        item,
        *,
        sample_index: int,
        label: str,
        x_values,
        y_values,
    ) -> None:
        x = np.asarray(x_values, dtype=float)
        y = np.asarray(y_values, dtype=float)
        count = min(int(x.size), int(y.size))
        if count <= 0:
            return
        x = x[:count]
        y = y[:count]
        mask = np.isfinite(x) & np.isfinite(y)
        if not np.any(mask):
            return
        entry = {
            "item": item,
            "sample_index": int(sample_index),
            "label": str(label),
            "x": x[mask],
            "y": y[mask],
            "base_pen": _copy_pen_option(item.opts.get("pen")),
            "base_symbol_pen": _copy_pen_option(item.opts.get("symbolPen")),
            "base_shadow_pen": _copy_pen_option(item.opts.get("shadowPen")),
            "base_symbol_size": item.opts.get("symbolSize"),
            "base_z": float(item.zValue()),
        }
        self.entries.append(entry)
        try:
            item.setCurveClickable(True, width=max(10, int(self.HIT_DISTANCE_PX * 1.6)))
            item.sigClicked.connect(
                lambda _item, event, index=int(sample_index), controller=self: controller._on_curve_clicked(index, event)
            )
        except Exception:
            pass

    def _on_mouse_moved(self, scene_pos) -> None:
        legend_sample_index = _legend_sample_at_scene_pos(self.plot, scene_pos)
        if legend_sample_index is not None:
            self.pending_scene_pos = None
            self.hover_timer.stop()
            self._hide_tooltip()
            self.set_hover_sample(int(legend_sample_index), propagate=True)
            return
        if _legend_contains_scene_pos(self.plot, scene_pos):
            self.clear_hover()
            return
        if not self.view_box.sceneBoundingRect().contains(scene_pos):
            self.clear_hover()
            return
        self.pending_scene_pos = QtCore.QPointF(scene_pos)
        if self.HOVER_DELAY_MS <= 0:
            self._resolve_hover()
        else:
            self.hover_timer.start()

    def _on_mouse_clicked(self, event) -> None:
        if event.button() != QtCore.Qt.LeftButton:
            return
        scene_pos = event.scenePos()
        sample_index = _legend_sample_at_scene_pos(self.plot, scene_pos)
        if sample_index is None:
            return
        double_click = getattr(event, "double", False)
        is_double_click = double_click() if callable(double_click) else bool(double_click)
        if is_double_click:
            return
        if hasattr(event, "accept"):
            event.accept()
        cursor = getattr(self.plot, "_click_projection_cursor", None)
        if cursor is not None and hasattr(cursor, "clear"):
            cursor.clear()
        self.set_hover_sample(int(sample_index), propagate=True)
        self._select_sample_later(int(sample_index))

    def _resolve_hover(self) -> None:
        scene_pos = self.pending_scene_pos
        if scene_pos is None or not self.entries:
            self.clear_hover()
            return
        if not self.view_box.sceneBoundingRect().contains(scene_pos):
            self.clear_hover()
            return
        transform_context = self._scene_transform_context()
        if transform_context is None:
            self.clear_hover()
            return
        best_entry = None
        best_distance = math.inf
        best_view_point = None
        for entry in self.entries:
            distance, view_point = self._distance_to_entry(scene_pos, entry, transform_context)
            if distance < best_distance:
                best_distance = distance
                best_entry = entry
                best_view_point = view_point
        if best_entry is None or best_distance > self.HIT_DISTANCE_PX:
            self.clear_hover()
            return
        self._set_hover(best_entry, best_view_point)

    def _scene_transform_context(self):
        try:
            (x_min, x_max), (y_min, y_max) = self.view_box.viewRange()
            x_min = float(x_min)
            x_max = float(x_max)
            y_min = float(y_min)
            y_max = float(y_max)
            if not all(np.isfinite(value) for value in (x_min, x_max, y_min, y_max)):
                return None
            if abs(x_max - x_min) <= 1e-15 or abs(y_max - y_min) <= 1e-15:
                return None
            origin = self.view_box.mapViewToScene(QtCore.QPointF(x_min, y_min))
            x_ref = self.view_box.mapViewToScene(QtCore.QPointF(x_max, y_min))
            y_ref = self.view_box.mapViewToScene(QtCore.QPointF(x_min, y_max))
            x_scale = 1.0 / (x_max - x_min)
            y_scale = 1.0 / (y_max - y_min)
            return (
                x_min,
                y_min,
                float(origin.x()),
                float(origin.y()),
                (float(x_ref.x()) - float(origin.x())) * x_scale,
                (float(x_ref.y()) - float(origin.y())) * x_scale,
                (float(y_ref.x()) - float(origin.x())) * y_scale,
                (float(y_ref.y()) - float(origin.y())) * y_scale,
            )
        except Exception:
            return None

    @staticmethod
    def _view_to_scene_arrays(x_view: np.ndarray, y_view: np.ndarray, transform_context) -> tuple[np.ndarray, np.ndarray]:
        x_min, y_min, origin_x, origin_y, x_axis_x, x_axis_y, y_axis_x, y_axis_y = transform_context
        dx = x_view - x_min
        dy = y_view - y_min
        scene_x = origin_x + dx * x_axis_x + dy * y_axis_x
        scene_y = origin_y + dx * x_axis_y + dy * y_axis_y
        return scene_x, scene_y

    def _distance_to_entry(
        self,
        scene_pos: QtCore.QPointF,
        entry: dict[str, object],
        transform_context,
    ) -> tuple[float, QtCore.QPointF | None]:
        x = np.asarray(entry["x"], dtype=float)
        y = np.asarray(entry["y"], dtype=float)
        x_view, y_view = self._data_to_view_coordinates(x, y)
        mask = np.isfinite(x_view) & np.isfinite(y_view)
        if not np.any(mask):
            return (math.inf, None)
        x_view = x_view[mask]
        y_view = y_view[mask]
        scene_x, scene_y = self._view_to_scene_arrays(x_view, y_view, transform_context)
        mask = np.isfinite(scene_x) & np.isfinite(scene_y)
        if not np.any(mask):
            return (math.inf, None)
        x_view = x_view[mask]
        y_view = y_view[mask]
        scene_x = scene_x[mask]
        scene_y = scene_y[mask]
        px = float(scene_pos.x())
        py = float(scene_pos.y())
        if scene_x.size == 1:
            return (
                math.hypot(float(scene_x[0]) - px, float(scene_y[0]) - py),
                QtCore.QPointF(float(x_view[0]), float(y_view[0])),
            )
        x1 = scene_x[:-1]
        y1 = scene_y[:-1]
        x2 = scene_x[1:]
        y2 = scene_y[1:]
        dx = x2 - x1
        dy = y2 - y1
        denom = dx * dx + dy * dy
        with np.errstate(divide="ignore", invalid="ignore"):
            t = ((px - x1) * dx + (py - y1) * dy) / denom
        t = np.where(denom > 1e-12, np.clip(t, 0.0, 1.0), 0.0)
        nearest_x = x1 + t * dx
        nearest_y = y1 + t * dy
        distances_sq = (nearest_x - px) ** 2 + (nearest_y - py) ** 2
        if distances_sq.size == 0 or not np.any(np.isfinite(distances_sq)):
            return (math.inf, None)
        idx = int(np.nanargmin(distances_sq))
        distance = math.sqrt(float(distances_sq[idx]))
        segment_t = float(t[idx])
        view_x = float(x_view[idx]) + segment_t * (float(x_view[idx + 1]) - float(x_view[idx]))
        view_y = float(y_view[idx]) + segment_t * (float(y_view[idx + 1]) - float(y_view[idx]))
        return (distance, QtCore.QPointF(view_x, view_y))

    def _data_to_view_coordinates(self, x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        x_view = np.asarray(x, dtype=float).copy()
        y_view = np.asarray(y, dtype=float).copy()
        if getattr(self.plot_item.getAxis("bottom"), "logMode", False):
            with np.errstate(divide="ignore", invalid="ignore"):
                x_view = np.where(x_view > 0.0, np.log10(x_view), np.nan)
        if getattr(self.plot_item.getAxis("left"), "logMode", False):
            with np.errstate(divide="ignore", invalid="ignore"):
                y_view = np.where(y_view > 0.0, np.log10(y_view), np.nan)
        return x_view, y_view

    def _set_hover(self, entry: dict[str, object], view_point: QtCore.QPointF | None) -> None:
        sample_index = int(entry.get("sample_index", -1))
        if self.hovered_entry is entry and self.hovered_sample_index == sample_index:
            self._move_tooltip(entry, view_point)
            return
        self._restore_all()
        self.hovered_entry = entry
        self.hovered_sample_index = sample_index
        self._apply_sample_highlight(sample_index, dim_others=True, glow=False)
        self._move_tooltip(entry, view_point)
        self._propagate_hover(sample_index)
        self._notify_hover(sample_index)

    def set_linked_hover(self, sample_index: int | None) -> None:
        if sample_index is None:
            self.clear_hover(propagate=False)
            return
        self.set_hover_sample(int(sample_index), propagate=False)

    def set_hover_sample(self, sample_index: int | None, *, propagate: bool = True) -> None:
        if sample_index is None:
            self.clear_hover(propagate=propagate)
            return
        sample_index = int(sample_index)
        if self.hovered_sample_index == sample_index:
            return
        for entry in self.entries:
            if int(entry.get("sample_index", -1)) == sample_index:
                self._set_linked_entry_hover(entry)
                if propagate:
                    self._propagate_hover(sample_index)
                    self._notify_hover(sample_index)
                return
        self.clear_hover(propagate=False)
        if propagate:
            self._propagate_hover(None)
            self._notify_hover(None)

    def _set_linked_entry_hover(self, entry: dict[str, object]) -> None:
        sample_index = int(entry.get("sample_index", -1))
        if self.hovered_sample_index == sample_index:
            return
        self._restore_all()
        self.hovered_entry = entry
        self.hovered_sample_index = sample_index
        self._apply_sample_highlight(sample_index, dim_others=True, glow=False)
        self._hide_tooltip()

    def set_selected_sample(self, sample_index: int | None) -> None:
        normalized_index = self._normalize_sample_index(sample_index)
        self.selected_sample_index = normalized_index
        if self.hovered_sample_index is not None:
            return
        self._restore_all()
        if normalized_index is None or not self._apply_sample_highlight(normalized_index, dim_others=False, glow=False):
            _set_legend_sample_hover(self.plot, None)

    @staticmethod
    def _normalize_sample_index(sample_index: int | None) -> int | None:
        if sample_index is None:
            return None
        try:
            index = int(sample_index)
        except (TypeError, ValueError):
            return None
        return index if index >= 0 else None

    def _apply_sample_highlight(
        self,
        sample_index: int,
        *,
        dim_others: bool,
        glow: bool,
    ) -> bool:
        highlighted_entries = [
            entry
            for entry in self.entries
            if int(entry.get("sample_index", -1)) == sample_index
        ]
        if not highlighted_entries:
            return False
        for other in self.entries:
            item = other.get("item")
            if item is None:
                continue
            try:
                if dim_others:
                    item.setOpacity(1.0 if int(other.get("sample_index", -1)) == sample_index else 0.22)
                else:
                    item.setOpacity(1.0)
            except Exception:
                pass
        for highlighted in highlighted_entries:
            self._highlight_entry_item(highlighted, glow=glow)
        _set_legend_sample_hover(self.plot, sample_index)
        return True

    def clear_hover(self, *, propagate: bool = True) -> None:
        self.hover_timer.stop()
        self.pending_scene_pos = None
        if (
            self.hovered_entry is None
            and self.hovered_sample_index is None
            and getattr(self.plot, "_sample_legend_hover_index", None) is None
            and not self._tooltip_is_visible()
        ):
            return
        self._restore_all()
        self.hovered_entry = None
        self.hovered_sample_index = None
        self._hide_tooltip()
        if self.selected_sample_index is None or not self._apply_sample_highlight(
            self.selected_sample_index,
            dim_others=False,
            glow=False,
        ):
            _set_legend_sample_hover(self.plot, None)
        if propagate:
            self._propagate_hover(None)
            self._notify_hover(None)

    def _restore_all(self) -> None:
        for entry in self.entries:
            item = entry.get("item")
            if item is None:
                continue
            try:
                item.setOpacity(1.0)
                if isinstance(entry.get("base_pen"), QtGui.QPen):
                    item.setPen(QtGui.QPen(entry["base_pen"]))
                if isinstance(entry.get("base_symbol_pen"), QtGui.QPen):
                    item.setSymbolPen(QtGui.QPen(entry["base_symbol_pen"]))
                if hasattr(item, "setShadowPen"):
                    if isinstance(entry.get("base_shadow_pen"), QtGui.QPen):
                        item.setShadowPen(QtGui.QPen(entry["base_shadow_pen"]))
                    else:
                        item.setShadowPen(None)
                if entry.get("base_symbol_size") is not None:
                    item.setSymbolSize(entry["base_symbol_size"])
                item.setZValue(float(entry.get("base_z", 0.0)))
            except Exception:
                pass

    def _highlight_entry_item(self, entry: dict[str, object], *, glow: bool = False) -> None:
        item = entry.get("item")
        if item is None:
            return
        base_pen = entry.get("base_pen")
        if isinstance(base_pen, QtGui.QPen):
            highlight_pen = QtGui.QPen(base_pen)
            highlight_pen.setWidthF(max(float(base_pen.widthF()) + 2.5, 5.0))
            item.setPen(highlight_pen)
            if hasattr(item, "setShadowPen"):
                if glow:
                    glow_pen = QtGui.QPen(base_pen)
                    glow_pen.setColor(QtGui.QColor("#fbbf24"))
                    glow_pen.setWidthF(max(float(base_pen.widthF()) + 8.0, 10.0))
                    item.setShadowPen(glow_pen)
                else:
                    item.setShadowPen(None)
        base_symbol_size = entry.get("base_symbol_size")
        if base_symbol_size is not None:
            try:
                item.setSymbolSize(float(base_symbol_size) + 3.0)
            except Exception:
                pass
        try:
            item.setZValue(15_000)
        except Exception:
            pass

    def _move_tooltip(self, entry: dict[str, object], view_point: QtCore.QPointF | None) -> None:
        if view_point is None:
            return
        tooltip = self._ensure_tooltip()
        added_items = getattr(self.view_box, "addedItems", [])
        if tooltip not in added_items:
            self.view_box.addItem(tooltip, ignoreBounds=True)
        tooltip.setText(str(entry.get("label", "")))
        tooltip.setPos(float(view_point.x()), float(view_point.y()))
        tooltip.show()

    def _on_curve_clicked(self, sample_index: int, event) -> None:
        if event.button() != QtCore.Qt.LeftButton:
            return
        double_click = getattr(event, "double", False)
        is_double_click = double_click() if callable(double_click) else bool(double_click)
        cursor = getattr(self.plot, "_click_projection_cursor", None)
        if is_double_click:
            if cursor is not None and hasattr(cursor, "clear"):
                cursor.clear()
            if hasattr(event, "accept"):
                event.accept()
            self._select_sample_later(int(sample_index))
            return
        if cursor is not None and hasattr(cursor, "set_scene_position"):
            cursor.set_scene_position(event.scenePos())

    def _select_sample_later(self, sample_index: int) -> None:
        callback = getattr(self.plot, "_sample_curve_selected_callback", None)
        if not callable(callback):
            return
        # Let pyqtgraph finish dispatching the click before the main window
        # redraws plots and may replace scene items.
        QtCore.QTimer.singleShot(
            0,
            lambda index=int(sample_index), selected_callback=callback: selected_callback(index),
        )

    def _propagate_hover(self, sample_index: int | None) -> None:
        for peer_plot in getattr(self.plot, "_linked_sample_curve_hover_plots", []):
            if peer_plot is self.plot:
                continue
            controller = getattr(peer_plot, "_sample_curve_interaction_controller", None)
            if controller is None:
                continue
            try:
                controller.set_linked_hover(sample_index)
            except Exception:
                pass

    def _notify_hover(self, sample_index: int | None) -> None:
        callback = getattr(self.plot, "_sample_curve_hovered_callback", None)
        if callable(callback):
            try:
                callback(sample_index)
            except Exception:
                pass


def _sample_curve_controller(plot: pg.PlotWidget) -> SampleCurveInteractionController:
    controller = getattr(plot, "_sample_curve_interaction_controller", None)
    if controller is None:
        controller = SampleCurveInteractionController(plot)
        setattr(plot, "_sample_curve_interaction_controller", controller)
    return controller


def link_sample_curve_hover_plots(*plots: pg.PlotWidget) -> None:
    unique_plots = []
    for plot in plots:
        if plot is not None and plot not in unique_plots:
            unique_plots.append(plot)
    for plot in unique_plots:
        setattr(plot, "_linked_sample_curve_hover_plots", [peer for peer in unique_plots if peer is not plot])


def set_sample_curve_hover_plots(sample_index: int | None, *plots: pg.PlotWidget) -> None:
    for plot in plots:
        controller = getattr(plot, "_sample_curve_interaction_controller", None)
        if controller is None:
            _set_legend_sample_hover(plot, sample_index)
            continue
        try:
            controller.set_linked_hover(sample_index)
        except Exception:
            pass


def set_sample_curve_selected_plots(sample_index: int | None, *plots: pg.PlotWidget) -> None:
    for plot in plots:
        controller = getattr(plot, "_sample_curve_interaction_controller", None)
        if controller is None:
            _set_legend_sample_hover(plot, sample_index)
            continue
        try:
            controller.set_selected_sample(sample_index)
        except Exception:
            pass


def _copy_pen_option(value) -> QtGui.QPen | None:
    if value is None:
        return None
    if isinstance(value, QtGui.QPen):
        return QtGui.QPen(value)
    try:
        return QtGui.QPen(pg.mkPen(value))
    except Exception:
        return None


def _reset_sample_curve_interactions(plot: pg.PlotWidget) -> None:
    controller = getattr(plot, "_sample_curve_interaction_controller", None)
    if controller is not None:
        controller.reset()
    setattr(plot, "_sample_legend_graphics_entries", [])
    setattr(plot, "_sample_legend_hover_index", None)


def _register_sample_curve(
    plot: pg.PlotWidget,
    item,
    *,
    sample_index: int,
    label: str,
    x_values,
    y_values,
) -> None:
    if item is None:
        return
    _sample_curve_controller(plot).register(
        item,
        sample_index=sample_index,
        label=label,
        x_values=x_values,
        y_values=y_values,
    )


def _set_sample_legend_entries(plot: pg.PlotWidget, entries) -> None:
    legend = getattr(plot.plotItem, "legend", None)
    if legend is None:
        return
    legend.clear()
    sorted_entries = sorted(entries, key=lambda entry: entry[0])
    for _index, item, name in sorted_entries:
        legend.addItem(item, name)
    graphics_entries = []
    for source_entry, legend_entry in zip(sorted_entries, list(getattr(legend, "items", []))):
        try:
            index, item, name = source_entry
            sample_item, label_item = legend_entry
            label_graphics_item = getattr(label_item, "item", None)
            base_font = (
                label_graphics_item.font()
                if label_graphics_item is not None and hasattr(label_graphics_item, "font")
                else label_item.font()
            )
            graphics_entries.append(
                {
                    "sample_index": int(index),
                    "curve_item": item,
                    "name": str(name),
                    "sample_item": sample_item,
                    "label_item": label_item,
                    "base_font": QtGui.QFont(base_font),
                }
            )
        except Exception:
            continue
    setattr(plot, "_sample_legend_graphics_entries", graphics_entries)
    setattr(plot, "_sample_legend_hover_index", None)
    _refresh_legend_layout(plot)
    _sync_plot_legend_visibility(plot)


def make_plot(
    title: str,
    left_label: str,
    bottom_label: str,
    *,
    legend_position: str = "left",
) -> pg.PlotWidget:
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
    setattr(plot, "_legend_default_position", "right" if legend_position == "right" else "left")
    setattr(plot, "_sample_legend_graphics_entries", [])
    setattr(plot, "_sample_legend_hover_index", None)
    _enable_click_projection_cursor(plot)
    _install_legend_toggle(plot)
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
    _reset_sample_curve_interactions(plot)
    plot.setLogMode(x=True, y=False)
    all_x = []
    all_y = []
    legend_entries = []
    for index, result in enumerate(results):
        if index >= len(visible) or not visible[index]:
            continue
        color = colors[index % len(colors)]
        finite = np.isfinite(result.pressure) & np.isfinite(result.cum_volume)
        intrusion = finite & (result.is_extrusion < 0.5)
        extrusion = finite & (result.is_extrusion >= 0.5)
        label = _legend_name(result)

        intrusion_item = _plot_cycle(plot, result, intrusion, color, label, symbol_brush=color)
        extrusion_item = _plot_cycle(plot, result, extrusion, color, None, style=QtDashLine(), symbol_brush="#ffffff")
        _register_sample_curve(
            plot,
            intrusion_item,
            sample_index=index,
            label=label,
            x_values=result.pressure[intrusion],
            y_values=result.cum_volume[intrusion],
        )
        _register_sample_curve(
            plot,
            extrusion_item,
            sample_index=index,
            label=label,
            x_values=result.pressure[extrusion],
            y_values=result.cum_volume[extrusion],
        )
        if intrusion_item is not None:
            legend_entries.append((index, intrusion_item, label))

        if np.any(finite):
            all_x.append(result.pressure[finite])
            all_y.append(result.cum_volume[finite])

    _set_sample_legend_entries(plot, legend_entries)
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
    _reset_sample_curve_interactions(plot)
    plot.setLogMode(x=False, y=False)
    all_x = []
    all_y = []
    legend_entries = []
    curve_data_by_index: list[dict[str, np.ndarray] | None] = [None] * len(results)
    for index, result in enumerate(results):
        if index >= len(visible) or not visible[index]:
            continue
        color = colors[index % len(colors)]
        data = distribution_plot_data(result)
        curve_data_by_index[index] = data
        label = _legend_name(result)
        curve_item = plot.plot(
            data["curve_x"],
            data["curve_y"],
            pen=pg.mkPen(color, width=2),
            name=label,
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
        _register_sample_curve(
            plot,
            curve_item,
            sample_index=index,
            label=label,
            x_values=data["curve_x"],
            y_values=data["curve_y"],
        )
        _register_sample_curve(
            plot,
            point_item,
            sample_index=index,
            label=label,
            x_values=data["x"],
            y_values=data["y"],
        )
        legend_entries.append((index, curve_item, label))
        if data["x"].size:
            all_x.append(data["x"])
            all_y.append(data["y"])

    plot.setLabel("bottom", "孔径 (nm)")
    _set_sample_legend_entries(plot, legend_entries)
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
) -> object | None:
    x_values = result.pressure[mask]
    y_values = result.cum_volume[mask]
    if not x_values.size:
        return None
    pen = pg.mkPen(color, width=2)
    if style is not None:
        pen.setStyle(style)
    return plot.plot(
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
    return str(result.metadata.get("file_name") or result.sample_name or "样品")


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
