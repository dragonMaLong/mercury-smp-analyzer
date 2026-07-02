from __future__ import annotations

import os
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Iterable

import numpy as np

os.environ.setdefault("PYQTGRAPH_QT_LIB", "PyQt5")

import pyqtgraph as pg
from pyqtgraph.Qt import QT_LIB, QtCore, QtGui, QtWidgets

from mercury_app.core import calculate_microactive, export_results_xlsx, load_smp, metrics_for_pressure_range, summary_metrics
from mercury_app.update_checker import DEFAULT_UPDATE_REPOSITORY, UpdateInfo, check_for_update
from mercury_app.updater import UpdateDownloadError, download_update, launch_update_and_exit
from mercury_app.ui.plots import (
    DEFAULT_COLORS,
    clip_log_curve_to_range,
    link_sample_curve_hover_plots,
    make_plot,
    plot_distribution_multi,
    plot_pressure_volume_multi,
    set_sample_curve_selected_plots,
    set_sample_curve_hover_plots,
)
from mercury_app.version import __version__


APP_NAME = "MIP综合分析-DragonScience"
APP_VERSION = __version__
APP_TITLE = f"{APP_NAME} v{APP_VERSION}"
APP_USER_MODEL_ID = "MercurySmpAnalyzer.Zh"
APP_ICON_FILE = "mip-dragon-science-logo.ico"
UPDATE_REPOSITORY = DEFAULT_UPDATE_REPOSITORY
AUTO_UPDATE_CHECK_DELAY_MS = 3000
SUPPORTED_SMP_SUFFIXES = (".smp",)


Signal = getattr(QtCore, "Signal", None) or getattr(QtCore, "pyqtSignal")


def _qt_enum_int(value) -> int:
    try:
        return int(value)
    except TypeError:
        return int(value.value)


def _make_update_available_icon(size: int = 28) -> QtGui.QIcon:
    pixmap = QtGui.QPixmap(size, size)
    pixmap.fill(QtCore.Qt.transparent)
    painter = QtGui.QPainter(pixmap)
    painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
    scale = size / 28.0

    def s(value: float) -> float:
        return float(value) * scale

    cloud = QtGui.QPainterPath()
    cloud.moveTo(s(6.4), s(22.0))
    cloud.cubicTo(s(3.3), s(22.0), s(1.4), s(19.8), s(1.4), s(17.1))
    cloud.cubicTo(s(1.4), s(14.4), s(3.4), s(12.3), s(6.0), s(12.2))
    cloud.cubicTo(s(6.8), s(8.6), s(9.9), s(5.9), s(13.8), s(5.9))
    cloud.cubicTo(s(17.0), s(5.9), s(19.7), s(7.8), s(21.0), s(10.8))
    cloud.cubicTo(s(24.2), s(11.1), s(26.6), s(13.5), s(26.6), s(16.6))
    cloud.cubicTo(s(26.6), s(19.6), s(24.2), s(22.0), s(21.0), s(22.0))
    cloud.lineTo(s(6.4), s(22.0))
    cloud.closeSubpath()
    painter.setPen(QtCore.Qt.NoPen)
    painter.setBrush(QtGui.QColor("#2563eb"))
    painter.drawPath(cloud)

    arrow = QtGui.QPainterPath()
    arrow.setFillRule(QtCore.Qt.WindingFill)
    arrow.moveTo(s(14.0), s(8.6))
    arrow.cubicTo(s(12.9), s(8.6), s(12.0), s(9.5), s(12.0), s(10.6))
    arrow.lineTo(s(12.0), s(15.4))
    arrow.lineTo(s(8.4), s(15.4))
    arrow.lineTo(s(14.0), s(21.0))
    arrow.lineTo(s(19.6), s(15.4))
    arrow.lineTo(s(16.0), s(15.4))
    arrow.lineTo(s(16.0), s(10.6))
    arrow.cubicTo(s(16.0), s(9.5), s(15.1), s(8.6), s(14.0), s(8.6))
    arrow.closeSubpath()
    painter.setBrush(QtGui.QColor("#ffffff"))
    painter.drawPath(arrow)
    painter.end()
    return QtGui.QIcon(pixmap)


class UpdateCheckWorker(QtCore.QObject):
    finished = Signal(object, bool)
    failed = Signal(str, bool)

    def __init__(self, current_version: str, repository: str, manual: bool) -> None:
        super().__init__()
        self.current_version = current_version
        self.repository = repository
        self.manual = manual

    def run(self) -> None:
        try:
            info = check_for_update(self.current_version, repository=self.repository)
        except Exception as exc:
            self.failed.emit(str(exc), self.manual)
            return
        self.finished.emit(info, self.manual)


class UpdateDownloadWorker(QtCore.QObject):
    progress = Signal(int, int)
    finished = Signal(object, str)
    failed = Signal(str)

    def __init__(self, info: UpdateInfo) -> None:
        super().__init__()
        self.info = info

    def run(self) -> None:
        try:
            path = download_update(
                self.info,
                progress_callback=lambda downloaded, total: self.progress.emit(downloaded, total),
            )
        except Exception as exc:
            self.failed.emit(str(exc))
            return
        self.finished.emit(self.info, str(path))


class SelectAllCheckBox(QtWidgets.QCheckBox):
    def nextCheckState(self) -> None:
        if self.checkState() == QtCore.Qt.Checked:
            self.setCheckState(QtCore.Qt.Unchecked)
        else:
            self.setCheckState(QtCore.Qt.Checked)


class SampleTableWidget(QtWidgets.QTableWidget):
    rowHovered = Signal(int)
    rowMoveRequested = Signal(int, int)
    smpFilesDropped = Signal(list)
    LONG_PRESS_MS = 220
    FROZEN_COLUMN_COUNT = 2

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)
        self._syncing_frozen_columns = False
        self._hovered_row = -1
        self._drag_source_row = -1
        self._drag_start_pos = QtCore.QPoint()
        self._drag_timer = QtCore.QElapsedTimer()
        self._dragging_row = False
        self._drop_indicator = QtWidgets.QFrame(self.viewport())
        self._drop_indicator.setFixedHeight(2)
        self._drop_indicator.setStyleSheet("background: #2563eb;")
        self._drop_indicator.hide()
        self._init_frozen_columns()

    def frozen_header(self):
        return self._frozen_table.horizontalHeader()

    def _init_frozen_columns(self) -> None:
        self._frozen_table = QtWidgets.QTableView(self)
        self._frozen_table.setModel(self.model())
        self._frozen_table.setSelectionModel(self.selectionModel())
        self._frozen_table.setAcceptDrops(True)
        self._frozen_table.viewport().setAcceptDrops(True)
        self._frozen_table.setFocusPolicy(QtCore.Qt.NoFocus)
        self._frozen_table.setFrameShape(QtWidgets.QFrame.NoFrame)
        self._frozen_table.setShowGrid(False)
        self._frozen_table.setAlternatingRowColors(False)
        self._frozen_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self._frozen_table.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self._frozen_table.setEditTriggers(
            QtWidgets.QAbstractItemView.DoubleClicked
            | QtWidgets.QAbstractItemView.EditKeyPressed
            | QtWidgets.QAbstractItemView.AnyKeyPressed
        )
        self._frozen_table.setVerticalScrollMode(QtWidgets.QAbstractItemView.ScrollPerPixel)
        self._frozen_table.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self._frozen_table.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self._frozen_table.setMouseTracking(True)
        self._frozen_table.viewport().setMouseTracking(True)
        self._frozen_table.setStyleSheet(
            """
            QTableView {
                border: 0;
                background: #ffffff;
                alternate-background-color: #ffffff;
            }
            QTableView::item:selected {
                background: #e0ecff;
                color: #111827;
            }
            QTableView::item:focus {
                outline: none;
            }
            QTableView::indicator {
                width: 11px;
                height: 11px;
                border-radius: 6px;
                border: 1px solid #6b7280;
                background: white;
            }
            QTableView::indicator:checked {
                border: 1px solid #2563eb;
                background: #2563eb;
            }
            QHeaderView::section {
                background: #f9fafb;
                border: 0;
                border-right: 1px solid #d1d5db;
                border-bottom: 1px solid #d1d5db;
                color: #374151;
                font-weight: 600;
                padding: 4px 8px 4px 6px;
            }
            """
        )
        self._frozen_table.verticalHeader().hide()
        self._frozen_table.verticalHeader().setDefaultSectionSize(self.verticalHeader().defaultSectionSize())
        frozen_header = self._frozen_table.horizontalHeader()
        frozen_header.setSectionsMovable(False)
        frozen_header.setHighlightSections(False)
        frozen_header.setDefaultAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
        frozen_header.setSectionResizeMode(QtWidgets.QHeaderView.Interactive)
        self.viewport().installEventFilter(self)
        self._frozen_table.viewport().installEventFilter(self)

        for column in range(self.model().columnCount()):
            self._frozen_table.setColumnHidden(column, column >= self.FROZEN_COLUMN_COUNT)

        self.horizontalHeader().sectionResized.connect(self._on_main_section_resized)
        frozen_header.sectionResized.connect(self._on_frozen_section_resized)
        self.verticalHeader().sectionResized.connect(self._on_main_row_resized)
        self.verticalScrollBar().valueChanged.connect(self._frozen_table.verticalScrollBar().setValue)
        self._frozen_table.verticalScrollBar().valueChanged.connect(self.verticalScrollBar().setValue)
        self._frozen_table.show()
        self.sync_frozen_row_heights()
        self._update_frozen_geometry()

    def setRowCount(self, rows: int) -> None:
        super().setRowCount(rows)
        self.sync_frozen_row_heights()

    def sync_frozen_row_heights(self) -> None:
        if not hasattr(self, "_frozen_table"):
            return
        self._frozen_table.verticalHeader().setDefaultSectionSize(self.verticalHeader().defaultSectionSize())
        for row in range(self.rowCount()):
            self._frozen_table.setRowHeight(row, self.rowHeight(row))

    def eventFilter(self, obj, event) -> bool:
        frozen_table = getattr(self, "_frozen_table", None)
        if obj is self.viewport():
            if event.type() == QtCore.QEvent.MouseMove:
                self._set_hovered_row(self.rowAt(event.pos().y()))
            elif event.type() in (QtCore.QEvent.Leave, QtCore.QEvent.Hide):
                self._set_hovered_row(-1)
            return super().eventFilter(obj, event)

        if frozen_table is not None and obj is frozen_table.viewport():
            if event.type() == QtCore.QEvent.MouseMove:
                self._set_hovered_row(self._frozen_table.rowAt(event.pos().y()))
            if event.type() in (QtCore.QEvent.DragEnter, QtCore.QEvent.DragMove):
                if self._accept_file_drag_event(event):
                    return True
            if event.type() == QtCore.QEvent.Drop:
                if self._accept_file_drop_event(event):
                    return True
            if event.type() == QtCore.QEvent.ContextMenu:
                self.customContextMenuRequested.emit(QtCore.QPoint(0, event.pos().y()))
                return True
            if event.type() == QtCore.QEvent.MouseButtonPress and event.button() == QtCore.Qt.LeftButton:
                self._begin_row_drag(self._frozen_to_main_viewport_pos(event.pos()))
                return False
            if event.type() == QtCore.QEvent.MouseMove:
                if self._update_row_drag(self._frozen_to_main_viewport_pos(event.pos()), event.buttons()):
                    return True
            if event.type() == QtCore.QEvent.MouseButtonRelease:
                if self._finish_row_drag(self._frozen_to_main_viewport_pos(event.pos()), event.button()):
                    return True
                if event.button() == QtCore.Qt.LeftButton:
                    self._reset_row_drag()
                return False
            if event.type() in (QtCore.QEvent.Leave, QtCore.QEvent.Hide):
                self._set_hovered_row(-1)
        return super().eventFilter(obj, event)

    def _frozen_to_main_viewport_pos(self, position: QtCore.QPoint) -> QtCore.QPoint:
        return self.viewport().mapFromGlobal(self._frozen_table.viewport().mapToGlobal(position))

    def scrollTo(self, index, hint=QtWidgets.QAbstractItemView.EnsureVisible) -> None:
        if not index.isValid():
            return
        horizontal_value = self.horizontalScrollBar().value()
        super().scrollTo(index, hint)
        if index.column() < self.FROZEN_COLUMN_COUNT or self.selectionBehavior() == QtWidgets.QAbstractItemView.SelectRows:
            self.horizontalScrollBar().setValue(horizontal_value)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._update_frozen_geometry()

    def setColumnWidth(self, column: int, width: int) -> None:
        super().setColumnWidth(column, width)
        if hasattr(self, "_frozen_table") and column < self.FROZEN_COLUMN_COUNT:
            self._frozen_table.setColumnWidth(column, width)
            self._update_frozen_geometry()

    def setRowHeight(self, row: int, height: int) -> None:
        super().setRowHeight(row, height)
        if hasattr(self, "_frozen_table"):
            self._frozen_table.setRowHeight(row, height)

    def _on_main_section_resized(self, logical_index: int, old_size: int, new_size: int) -> None:
        if logical_index >= self.FROZEN_COLUMN_COUNT or self._syncing_frozen_columns:
            self._update_frozen_geometry()
            return
        self._syncing_frozen_columns = True
        try:
            self._frozen_table.setColumnWidth(logical_index, new_size)
        finally:
            self._syncing_frozen_columns = False
        self._update_frozen_geometry()

    def _on_frozen_section_resized(self, logical_index: int, old_size: int, new_size: int) -> None:
        if logical_index >= self.FROZEN_COLUMN_COUNT or self._syncing_frozen_columns:
            return
        self._syncing_frozen_columns = True
        try:
            super().setColumnWidth(logical_index, new_size)
        finally:
            self._syncing_frozen_columns = False
        self._update_frozen_geometry()

    def _on_main_row_resized(self, logical_index: int, old_size: int, new_size: int) -> None:
        self._frozen_table.setRowHeight(logical_index, new_size)

    def _frozen_width(self) -> int:
        return sum(self.columnWidth(column) for column in range(self.FROZEN_COLUMN_COUNT))

    def _update_frozen_geometry(self) -> None:
        if not hasattr(self, "_frozen_table"):
            return
        width = self._frozen_width()
        self._frozen_table.setGeometry(
            self.frameWidth(),
            self.frameWidth(),
            width,
            self.viewport().height() + self.horizontalHeader().height(),
        )
        self._frozen_table.raise_()

    def mousePressEvent(self, event) -> None:
        if event.button() == QtCore.Qt.LeftButton:
            self._begin_row_drag(event.pos())
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        self._set_hovered_row(self.rowAt(event.pos().y()))
        if not self._update_row_drag(event.pos(), event.buttons()):
            super().mouseMoveEvent(event)
            return
        event.accept()

    def mouseReleaseEvent(self, event) -> None:
        if self._finish_row_drag(event.pos(), event.button()):
            event.accept()
            return
        if event.button() == QtCore.Qt.LeftButton:
            self._reset_row_drag()
        super().mouseReleaseEvent(event)

    def leaveEvent(self, event) -> None:
        self._set_hovered_row(-1)
        if not self._dragging_row:
            self._drop_indicator.hide()
        super().leaveEvent(event)

    def _set_hovered_row(self, row: int) -> None:
        try:
            row = int(row)
        except (TypeError, ValueError):
            row = -1
        row = row if 0 <= row < self.rowCount() else -1
        if row == self._hovered_row:
            return
        self._hovered_row = row
        self.rowHovered.emit(row)

    def _begin_row_drag(self, position: QtCore.QPoint) -> None:
        row = self.rowAt(position.y())
        if row >= 0:
            self._drag_source_row = row
            self._drag_start_pos = position
            self._drag_timer.start()
            self._dragging_row = False
            return
        self._reset_row_drag()

    def _update_row_drag(self, position: QtCore.QPoint, buttons) -> bool:
        if not (buttons & QtCore.Qt.LeftButton) or self._drag_source_row < 0:
            return False
        distance = (position - self._drag_start_pos).manhattanLength()
        if not self._dragging_row:
            if self._drag_timer.elapsed() < self.LONG_PRESS_MS or distance < QtWidgets.QApplication.startDragDistance():
                return True
            self._dragging_row = True
            self.setCursor(QtCore.Qt.ClosedHandCursor)
            if hasattr(self, "_frozen_table"):
                self._frozen_table.setCursor(QtCore.Qt.ClosedHandCursor)

        insert_row = self._drop_insert_row(position)
        self._show_drop_indicator(insert_row)
        self._auto_scroll(position)
        return True

    def _finish_row_drag(self, position: QtCore.QPoint, button) -> bool:
        if button != QtCore.Qt.LeftButton or not self._dragging_row:
            return False
        source_row = self._drag_source_row
        insert_row = self._drop_insert_row(position)
        self._reset_row_drag()
        if source_row >= 0:
            self.rowMoveRequested.emit(source_row, insert_row)
        return True

    def _reset_row_drag(self) -> None:
        if self._dragging_row:
            self.unsetCursor()
            if hasattr(self, "_frozen_table"):
                self._frozen_table.unsetCursor()
        self._drag_source_row = -1
        self._dragging_row = False
        self._drop_indicator.hide()

    def _drop_insert_row(self, position: QtCore.QPoint) -> int:
        row_count = self.rowCount()
        if row_count == 0:
            return 0
        row = self.rowAt(position.y())
        if row < 0:
            return 0 if position.y() < 0 else row_count
        midpoint = self.rowViewportPosition(row) + self.rowHeight(row) / 2
        return row if position.y() < midpoint else row + 1

    def _show_drop_indicator(self, insert_row: int) -> None:
        row_count = self.rowCount()
        if row_count == 0:
            self._drop_indicator.hide()
            return
        if insert_row <= 0:
            y = self.rowViewportPosition(0)
        elif insert_row >= row_count:
            last_row = row_count - 1
            y = self.rowViewportPosition(last_row) + self.rowHeight(last_row)
        else:
            y = self.rowViewportPosition(insert_row)
        self._drop_indicator.setGeometry(0, max(0, int(y) - 1), self.viewport().width(), 2)
        self._drop_indicator.show()
        self._drop_indicator.raise_()

    def _auto_scroll(self, position: QtCore.QPoint) -> None:
        margin = 24
        step = 18
        scroll_bar = self.verticalScrollBar()
        if position.y() < margin:
            scroll_bar.setValue(scroll_bar.value() - step)
        elif position.y() > self.viewport().height() - margin:
            scroll_bar.setValue(scroll_bar.value() + step)

    def dragEnterEvent(self, event) -> None:
        if self._accept_file_drag_event(event):
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:
        if self._accept_file_drag_event(event):
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:
        if self._accept_file_drop_event(event):
            return
        super().dropEvent(event)

    def _accept_file_drag_event(self, event) -> bool:
        paths = self._smp_paths_from_mime_data(event.mimeData())
        if paths:
            event.acceptProposedAction()
            return True
        return False

    def _accept_file_drop_event(self, event) -> bool:
        paths = self._smp_paths_from_mime_data(event.mimeData())
        if not paths:
            return False
        event.acceptProposedAction()
        self.smpFilesDropped.emit(paths)
        return True

    @staticmethod
    def _smp_paths_from_mime_data(mime_data) -> list[str]:
        if not mime_data.hasUrls():
            return []
        paths = []
        for url in mime_data.urls():
            if not url.isLocalFile():
                continue
            path = Path(url.toLocalFile())
            if path.is_file() and path.suffix.lower() in SUPPORTED_SMP_SUFFIXES:
                paths.append(str(path))
        return paths


def _check_state_value(state) -> int:
    value = getattr(state, "value", state)
    return int(value)


VISIBLE_COLUMN = 0
FILE_COLUMN = 1
TEST_TIME_COLUMN = 2
ANGLE_COLUMN = 3
TENSION_COLUMN = 4
SELECTED_PORE_VOLUME_COLUMN = 5
HOVER_BASE_FONT_ROLE = QtCore.Qt.UserRole + 301
HOVER_BASE_FOREGROUND_ROLE = QtCore.Qt.UserRole + 302
REGION_LINE_COLOR = "#2563eb"
REGION_LINE_HOVER_COLOR = "#dc2626"
REGION_FILL_COLOR = (37, 99, 235, 34)
REGION_FILL_HOVER_COLOR = (37, 99, 235, 48)
DISTRIBUTION_REGION_LINE_COLOR = "#16a34a"
DISTRIBUTION_REGION_LINE_HOVER_COLOR = "#15803d"
DISTRIBUTION_REGION_FILL_COLOR = (22, 163, 74, 34)
DISTRIBUTION_REGION_FILL_HOVER_COLOR = (22, 163, 74, 48)
DISTRIBUTION_REGION_LABEL_HIDE_MS = 1800
DEFAULT_DISTRIBUTION_DIAMETER_REGION = (20.0, 90.0)


def _region_pen(color: str) -> QtGui.QPen:
    pen = pg.mkPen(color, width=3)
    pen.setStyle(QtCore.Qt.DashLine)
    return pen


class RegionEndpointLabel(pg.TextItem):
    def __init__(self, index: int, edit_callback) -> None:
        super().__init__(
            text="",
            color="#064e3b",
            anchor=(0.5, 1.0),
            fill=pg.mkBrush(255, 255, 255, 245),
            border=pg.mkPen(DISTRIBUTION_REGION_LINE_COLOR),
        )
        self.index = int(index)
        self._edit_callback = edit_callback
        self.setZValue(20_000)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setAnchor((1.0 if self.index == 0 else 0.0, 1.0))

    def mouseClickEvent(self, event) -> None:
        if event.button() != QtCore.Qt.LeftButton:
            return
        if hasattr(event, "accept"):
            event.accept()
        self._edit_callback(self.index)


class RegionEndpointLineEdit(QtWidgets.QLineEdit):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.cancel_requested = None
        self.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
        self.setFrame(False)
        self.setFixedSize(48, 22)
        self.setStyleSheet(
            """
            QLineEdit {
                border: 1px solid #16a34a;
                border-radius: 0px;
                background: rgba(255, 255, 255, 245);
                color: #064e3b;
                padding: 0px 2px;
                selection-background-color: #0078d7;
                selection-color: #ffffff;
            }
            """
        )

    def keyPressEvent(self, event) -> None:
        if event.key() == QtCore.Qt.Key_Escape:
            if callable(self.cancel_requested):
                self.cancel_requested()
            event.accept()
            return
        super().keyPressEvent(event)


class FileImportDialog(QtWidgets.QDialog):
    def __init__(
        self,
        parent=None,
        initial_dir: Path | str | None = None,
        existing_paths: Iterable[str] | None = None,
        available_sort: tuple[int, object] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("导入 SMP 文件")
        self.resize(960, 620)
        self.setMinimumSize(820, 500)
        self.setAcceptDrops(True)
        self.current_directory = Path(initial_dir or Path.cwd())
        self._available_paths: list[Path] = []
        self._selected_paths: list[Path] = [Path(path) for path in (existing_paths or [])]
        self._available_sort_column = int(available_sort[0]) if available_sort is not None else 0
        self._available_sort_order = available_sort[1] if available_sort is not None else QtCore.Qt.AscendingOrder

        folder_label = QtWidgets.QLabel("文件夹")
        self.folder_edit = QtWidgets.QLineEdit(str(self.current_directory))
        self.folder_edit.returnPressed.connect(self._set_directory_from_edit)
        browse_button = QtWidgets.QToolButton()
        browse_button.setText("...")
        browse_button.setToolTip("选择文件夹")
        browse_button.clicked.connect(self._browse_directory)
        refresh_button = QtWidgets.QToolButton()
        refresh_button.setText("刷新")
        refresh_button.clicked.connect(self._scan_directory)

        folder_layout = QtWidgets.QHBoxLayout()
        folder_layout.addWidget(folder_label)
        folder_layout.addWidget(self.folder_edit, 1)
        folder_layout.addWidget(browse_button)
        folder_layout.addWidget(refresh_button)

        self.search_edit = QtWidgets.QLineEdit()
        self.search_edit.setPlaceholderText("按文件名筛选")
        self.search_edit.textChanged.connect(lambda _text: self._populate_tables())

        search_layout = QtWidgets.QHBoxLayout()
        search_layout.addWidget(QtWidgets.QLabel("筛选"))
        search_layout.addWidget(self.search_edit, 1)

        self.available_table = self._make_file_table()
        self.selected_table = self._make_file_table()
        self.available_table.setSortingEnabled(True)
        self.available_table.horizontalHeader().sortIndicatorChanged.connect(self._on_available_sort_changed)
        self.available_table.itemDoubleClicked.connect(lambda _item: self._move_selected_to_right())
        self.selected_table.itemDoubleClicked.connect(lambda _item: self._move_selected_to_left())

        available_box = self._make_group("可导入 SMP", self.available_table)
        selected_box = self._make_group("待导入 SMP", self.selected_table)

        self.to_right_button = self._arrow_button(">", "添加选中文件")
        self.to_left_button = self._arrow_button("<", "移回选中文件")
        self.all_right_button = self._arrow_button(">>", "添加全部文件")
        self.all_left_button = self._arrow_button("<<", "全部移回")
        self.to_right_button.clicked.connect(self._move_selected_to_right)
        self.to_left_button.clicked.connect(self._move_selected_to_left)
        self.all_right_button.clicked.connect(self._move_all_to_right)
        self.all_left_button.clicked.connect(self._move_all_to_left)

        move_layout = QtWidgets.QVBoxLayout()
        move_layout.addStretch(1)
        for button in (self.to_right_button, self.to_left_button, self.all_right_button, self.all_left_button):
            move_layout.addWidget(button)
        move_layout.addStretch(1)

        self.move_up_button = self._arrow_button("↑", "上移选中文件")
        self.move_down_button = self._arrow_button("↓", "下移选中文件")
        self.move_up_button.clicked.connect(lambda: self._move_selected_rows(-1))
        self.move_down_button.clicked.connect(lambda: self._move_selected_rows(1))
        order_layout = QtWidgets.QHBoxLayout()
        order_layout.addStretch(1)
        order_layout.addWidget(self.move_up_button)
        order_layout.addWidget(self.move_down_button)

        selected_panel = QtWidgets.QWidget()
        selected_layout = QtWidgets.QVBoxLayout(selected_panel)
        selected_layout.setContentsMargins(0, 0, 0, 0)
        selected_layout.addWidget(selected_box, 1)
        selected_layout.addLayout(order_layout)

        picker_layout = QtWidgets.QHBoxLayout()
        picker_layout.addWidget(available_box, 1)
        picker_layout.addLayout(move_layout)
        picker_layout.addWidget(selected_panel, 1)

        self.count_label = QtWidgets.QLabel("")
        self.import_button = QtWidgets.QPushButton("导入")
        self.import_button.clicked.connect(self.accept)
        cancel_button = QtWidgets.QPushButton("取消")
        cancel_button.clicked.connect(self.reject)

        bottom_layout = QtWidgets.QHBoxLayout()
        bottom_layout.addWidget(self.count_label, 1)
        bottom_layout.addWidget(self.import_button)
        bottom_layout.addWidget(cancel_button)

        main_layout = QtWidgets.QVBoxLayout(self)
        main_layout.setContentsMargins(12, 12, 12, 12)
        main_layout.setSpacing(10)
        main_layout.addLayout(folder_layout)
        main_layout.addLayout(search_layout)
        main_layout.addLayout(picker_layout, 1)
        main_layout.addLayout(bottom_layout)

        for table in (self.available_table, self.selected_table):
            table.itemSelectionChanged.connect(self._update_buttons)
        self._scan_directory()

    def selected_paths(self) -> list[str]:
        return [str(path) for path in self._selected_paths]

    def available_sort(self) -> tuple[int, object]:
        return (self._available_sort_column, self._available_sort_order)

    @staticmethod
    def _make_group(title: str, table: QtWidgets.QTableWidget) -> QtWidgets.QGroupBox:
        group = QtWidgets.QGroupBox(title)
        layout = QtWidgets.QVBoxLayout(group)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.addWidget(table)
        return group

    @staticmethod
    def _make_file_table() -> QtWidgets.QTableWidget:
        table = QtWidgets.QTableWidget(0, 4)
        table.setHorizontalHeaderLabels(["文件名", "格式", "修改时间", "大小"])
        table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        table.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        table.setAlternatingRowColors(True)
        table.setSortingEnabled(False)
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(26)
        table.horizontalHeader().setStretchLastSection(False)
        table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        table.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(3, QtWidgets.QHeaderView.ResizeToContents)
        return table

    @staticmethod
    def _arrow_button(text: str, tooltip: str) -> QtWidgets.QPushButton:
        button = QtWidgets.QPushButton(text)
        button.setFixedSize(44, 32)
        button.setToolTip(tooltip)
        return button

    def dragEnterEvent(self, event) -> None:
        if self._smp_paths_from_mime_data(event.mimeData()):
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:
        if self._smp_paths_from_mime_data(event.mimeData()):
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:
        paths = self._smp_paths_from_mime_data(event.mimeData())
        if paths:
            event.acceptProposedAction()
            self._add_to_selected([Path(path) for path in paths])
            return
        super().dropEvent(event)

    def _browse_directory(self) -> None:
        directory = QtWidgets.QFileDialog.getExistingDirectory(self, "选择 SMP 文件夹", str(self.current_directory))
        if not directory:
            return
        self.current_directory = Path(directory)
        self.folder_edit.setText(str(self.current_directory))
        self._scan_directory()

    def _set_directory_from_edit(self) -> None:
        directory = Path(self.folder_edit.text().strip())
        if not directory.is_dir():
            QtWidgets.QMessageBox.warning(self, "文件夹不存在", str(directory))
            self.folder_edit.setText(str(self.current_directory))
            return
        self.current_directory = directory
        self._scan_directory()

    def _scan_directory(self) -> None:
        directory = self.current_directory
        self.folder_edit.setText(str(directory))
        selected = {self._path_key(path) for path in self._selected_paths}
        try:
            files = [
                path
                for path in directory.iterdir()
                if path.is_file() and path.suffix.lower() in SUPPORTED_SMP_SUFFIXES and self._path_key(path) not in selected
            ]
        except OSError as exc:
            QtWidgets.QMessageBox.warning(self, "无法读取文件夹", str(exc))
            files = []
        self._available_paths = sorted(files, key=lambda path: path.name.lower())
        self._populate_tables()

    def _populate_tables(self) -> None:
        query = self.search_edit.text().strip().lower()
        available = [path for path in self._available_paths if query in path.name.lower()]
        self._fill_table(self.available_table, available)
        self._fill_table(self.selected_table, self._selected_paths)
        self._update_buttons()

    def _fill_table(self, table: QtWidgets.QTableWidget, paths: list[Path]) -> None:
        table.setSortingEnabled(False)
        table.setRowCount(0)
        for path in paths:
            row = table.rowCount()
            table.insertRow(row)
            values = [path.name, path.suffix.upper().lstrip("."), self._modified_text(path), self._size_text(path)]
            for column, value in enumerate(values):
                item = QtWidgets.QTableWidgetItem(value)
                item.setData(QtCore.Qt.UserRole, str(path))
                if column in {1, 3}:
                    item.setTextAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
                table.setItem(row, column, item)
        table.setSortingEnabled(table is self.available_table)
        if table is self.available_table:
            table.sortItems(self._available_sort_column, self._available_sort_order)

    def _on_available_sort_changed(self, column: int, order) -> None:
        self._available_sort_column = int(column)
        self._available_sort_order = order

    @staticmethod
    def _modified_text(path: Path) -> str:
        try:
            return datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
        except OSError:
            return ""

    @staticmethod
    def _size_text(path: Path) -> str:
        try:
            size = path.stat().st_size
        except OSError:
            return ""
        if size >= 1024 * 1024:
            return f"{size / (1024 * 1024):.1f} MB"
        if size >= 1024:
            return f"{size / 1024:.1f} KB"
        return f"{size} B"

    @staticmethod
    def _path_key(path: Path) -> str:
        try:
            return str(path.resolve()).lower()
        except OSError:
            return str(path).lower()

    @staticmethod
    def _smp_paths_from_mime_data(mime_data) -> list[str]:
        return SampleTableWidget._smp_paths_from_mime_data(mime_data)

    def _selected_table_paths(self, table: QtWidgets.QTableWidget) -> list[Path]:
        rows = sorted({index.row() for index in table.selectionModel().selectedRows()})
        paths = []
        for row in rows:
            item = table.item(row, 0)
            if item is not None:
                paths.append(Path(str(item.data(QtCore.Qt.UserRole))))
        return paths

    def _move_selected_to_right(self) -> None:
        self._add_to_selected(self._selected_table_paths(self.available_table))

    def _move_all_to_right(self) -> None:
        self._add_to_selected(list(self._available_paths))

    def _add_to_selected(self, paths: list[Path]) -> None:
        if not paths:
            return
        selected_keys = {self._path_key(path) for path in self._selected_paths}
        for path in paths:
            if path.suffix.lower() not in SUPPORTED_SMP_SUFFIXES or not path.is_file():
                continue
            key = self._path_key(path)
            if key not in selected_keys:
                self._selected_paths.append(path)
                selected_keys.add(key)
        moved = {self._path_key(path) for path in paths}
        self._available_paths = [path for path in self._available_paths if self._path_key(path) not in moved]
        self._populate_tables()

    def _move_selected_to_left(self) -> None:
        self._remove_from_selected(self._selected_table_paths(self.selected_table))

    def _move_all_to_left(self) -> None:
        self._remove_from_selected(list(self._selected_paths))

    def _remove_from_selected(self, paths: list[Path]) -> None:
        if not paths:
            return
        removed = {self._path_key(path) for path in paths}
        self._selected_paths = [path for path in self._selected_paths if self._path_key(path) not in removed]
        existing = {self._path_key(path) for path in self._available_paths}
        for path in paths:
            if path.exists() and path.suffix.lower() in SUPPORTED_SMP_SUFFIXES and self._path_key(path) not in existing:
                self._available_paths.append(path)
                existing.add(self._path_key(path))
        self._available_paths.sort(key=lambda path: path.name.lower())
        self._populate_tables()

    def _move_selected_rows(self, direction: int) -> None:
        rows = sorted({index.row() for index in self.selected_table.selectionModel().selectedRows()})
        if not rows or (direction < 0 and rows[0] == 0) or (direction > 0 and rows[-1] >= len(self._selected_paths) - 1):
            return
        if direction > 0:
            rows = list(reversed(rows))
        for row in rows:
            target = row + direction
            self._selected_paths[row], self._selected_paths[target] = self._selected_paths[target], self._selected_paths[row]
        selected_after = [row + direction for row in rows]
        self._populate_tables()
        self.selected_table.clearSelection()
        for row in selected_after:
            self.selected_table.selectRow(row)

    def _update_buttons(self) -> None:
        has_available_selection = bool(self.available_table.selectionModel().selectedRows())
        has_selected_selection = bool(self.selected_table.selectionModel().selectedRows())
        self.to_right_button.setEnabled(has_available_selection)
        self.to_left_button.setEnabled(has_selected_selection)
        self.all_right_button.setEnabled(bool(self._available_paths))
        self.all_left_button.setEnabled(bool(self._selected_paths))
        self.move_up_button.setEnabled(has_selected_selection and min(self._selected_rows(self.selected_table), default=0) > 0)
        self.move_down_button.setEnabled(
            has_selected_selection
            and max(self._selected_rows(self.selected_table), default=-1) < len(self._selected_paths) - 1
        )
        self.import_button.setEnabled(bool(self._selected_paths))
        self.count_label.setText(f"可导入 {len(self._available_paths)} 个，待导入 {len(self._selected_paths)} 个")

    @staticmethod
    def _selected_rows(table: QtWidgets.QTableWidget) -> list[int]:
        return sorted({index.row() for index in table.selectionModel().selectedRows()})


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.result = None
        self.results = []
        self.visible_results = []
        self.active_index = 0
        self._hovered_sample_row = -1
        self.metric_tables = []
        self.sample_items = []
        self.sample_colors = list(DEFAULT_COLORS)
        self._updating_sample_checks = False
        self.test_time_sort_ascending = False
        self.selected_pore_volume_sort_ascending = False
        self.region = None
        self.pressure_region_is_log = False
        self.distribution_curve_data = []
        self.distribution_region = None
        self.distribution_region_is_log = False
        self.distribution_region_labels = []
        self.distribution_region_editor = None
        self.distribution_region_editor_proxy = None
        self._editing_distribution_endpoint_index = None
        self._distribution_endpoint_editor_dirty = False
        self._distribution_endpoint_editor_finishing = False
        self.distribution_selected_curve = None
        self.distribution_selected_points = None
        self._editing_distribution_endpoint = False
        self.distribution_region_label_timer = QtCore.QTimer(self)
        self.distribution_region_label_timer.setSingleShot(True)
        self.distribution_region_label_timer.setInterval(DISTRIBUTION_REGION_LABEL_HIDE_MS)
        self.distribution_region_label_timer.timeout.connect(self._hide_distribution_region_labels)
        self._metrics_pending = False
        self._syncing_region_changes = False
        self._checking_for_updates = False
        self._update_thread = None
        self._update_worker = None
        self._update_download_thread = None
        self._update_download_worker = None
        self._update_progress_dialog = None
        self._available_update_info = None
        self.settings = QtCore.QSettings("DragonScience", "MercurySmpAnalyzerZh")
        self.import_directory = self._read_directory_setting("import_directory")
        self._import_available_sort = (
            int(self.settings.value("import_available_sort_column", 0)),
            self._sort_order_from_setting(self.settings.value("import_available_sort_order", _qt_enum_int(QtCore.Qt.AscendingOrder))),
        )

        self.setAcceptDrops(True)
        self.setWindowTitle(APP_TITLE)
        self.resize(1200, 760)

        open_button = QtWidgets.QPushButton("导入文件")
        open_button.clicked.connect(self.open_files)
        export_button = QtWidgets.QPushButton("导出文件")
        export_button.clicked.connect(self.export_xls)
        self.update_available_button = QtWidgets.QToolButton()
        self.update_available_button.setIcon(_make_update_available_icon(28))
        self.update_available_button.setIconSize(QtCore.QSize(24, 24))
        self.update_available_button.setFixedSize(32, 32)
        self.update_available_button.setAutoRaise(True)
        self.update_available_button.setCursor(QtCore.Qt.PointingHandCursor)
        self.update_available_button.setToolTip("发现新版本，点击更新")
        self.update_available_button.clicked.connect(self._show_pending_update_dialog)
        self.update_available_button.hide()
        self.update_button = QtWidgets.QPushButton("软件更新")
        self.update_button.setToolTip("联网检查 Gitee/GitHub 更新源中是否有新版")
        self.update_button.clicked.connect(self.check_for_updates)
        for button in (open_button, export_button, self.update_button):
            button.setFixedHeight(32)
            button.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)
            button.setFixedWidth(96)

        self.pressure_plot = make_plot(
            "压力 - 累计孔体积",
            "累计孔体积 (mL/g)",
            "压力 (psia)",
        )
        self._connect_pressure_log_controls()
        self.distribution_plot = make_plot(
            "孔径分布",
            "dV/dlogD (mL/g)",
            "孔径 (nm)",
            legend_position="right",
        )
        self._connect_distribution_log_controls()
        setattr(self.distribution_plot, "_click_projection_ignore_callback", self._ignore_distribution_coordinate_click)
        self.distribution_plot.getPlotItem().getViewBox().sigRangeChanged.connect(
            self._on_distribution_view_range_changed
        )
        for plot in (self.distribution_plot, self.pressure_plot):
            setattr(plot, "_sample_curve_selected_callback", self._select_sample_from_curve)
            setattr(plot, "_sample_curve_hovered_callback", self._set_hovered_sample_row)
        link_sample_curve_hover_plots(self.distribution_plot, self.pressure_plot)

        self.plot_splitter = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        self.plot_splitter.addWidget(self.distribution_plot)
        self.plot_splitter.addWidget(self.pressure_plot)
        self.plot_splitter.setChildrenCollapsible(False)
        self.plot_splitter.setHandleWidth(8)
        self.plot_splitter.setStretchFactor(0, 3)
        self.plot_splitter.setStretchFactor(1, 2)
        self.plot_splitter.setSizes([456, 304])
        self.plot_splitter.setStyleSheet(
            """
            QSplitter::handle:vertical {
                background: #e5e7eb;
                margin: 2px 0;
            }
            QSplitter::handle:vertical:hover {
                background: #93c5fd;
            }
            """
        )

        side_panel = QtWidgets.QWidget()
        side_layout = QtWidgets.QVBoxLayout(side_panel)
        side_layout.setContentsMargins(6, 6, 6, 6)
        side_layout.setSpacing(6)
        button_row = QtWidgets.QHBoxLayout()
        button_row.setContentsMargins(0, 0, 0, 0)
        button_row.setSpacing(6)
        button_row.addWidget(open_button)
        button_row.addWidget(export_button)
        button_row.addStretch(1)
        button_row.addWidget(self.update_available_button)
        button_row.addWidget(self.update_button)
        side_layout.addLayout(button_row)

        self.select_all_check = SelectAllCheckBox()
        self.select_all_check.setTristate(True)
        self.select_all_check.setCheckState(QtCore.Qt.Checked)
        self.select_all_check.setCursor(QtCore.Qt.PointingHandCursor)
        self.select_all_check.setToolTip("显示或隐藏全部样品")
        self.select_all_check.stateChanged.connect(self.on_select_all_changed)
        self.select_all_check.setStyleSheet(
            """
            QCheckBox::indicator {
                width: 12px;
                height: 12px;
                border-radius: 7px;
                border: 1px solid #6b7280;
                background: white;
            }
            QCheckBox::indicator:checked {
                border: 1px solid #2563eb;
                background: #2563eb;
            }
            QCheckBox::indicator:indeterminate {
                border: 1px solid #2563eb;
                background: #93c5fd;
            }
            """
        )

        self.sample_list = SampleTableWidget(0, 6)
        self.sample_list.setHorizontalHeaderLabels(["", "文件名", "测试时间", "接触角", "表面张力", "选区孔容(mL/g)"])
        sample_header = self.sample_list.horizontalHeader()
        sample_header.setVisible(True)
        sample_header.setSectionsMovable(False)
        sample_header.setHighlightSections(False)
        sample_header.setStretchLastSection(False)
        sample_header.setMinimumSectionSize(24)
        sample_header.setDefaultAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
        sample_header.setSectionResizeMode(QtWidgets.QHeaderView.Interactive)
        sample_header.sectionClicked.connect(self.on_sample_header_clicked)
        sample_header.sectionResized.connect(self._position_header_controls)
        self.sample_list.frozen_header().sectionResized.connect(self._position_header_controls)
        self.sample_list.horizontalHeaderItem(TEST_TIME_COLUMN).setToolTip("点击按测试时间排序")
        self.sample_list.horizontalHeaderItem(ANGLE_COLUMN).setTextAlignment(QtCore.Qt.AlignCenter)
        self.sample_list.horizontalHeaderItem(TENSION_COLUMN).setTextAlignment(QtCore.Qt.AlignCenter)
        self.sample_list.horizontalHeaderItem(SELECTED_PORE_VOLUME_COLUMN).setTextAlignment(
            QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter
        )
        self.sample_list.horizontalHeaderItem(SELECTED_PORE_VOLUME_COLUMN).setToolTip("点击按当前选区积分孔容排序")
        self.sample_list.verticalHeader().setVisible(False)
        self.sample_list.verticalHeader().setDefaultSectionSize(28)
        self.sample_list.setShowGrid(False)
        self.sample_list.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.sample_list.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.sample_list.setEditTriggers(
            QtWidgets.QAbstractItemView.DoubleClicked
            | QtWidgets.QAbstractItemView.EditKeyPressed
            | QtWidgets.QAbstractItemView.AnyKeyPressed
        )
        self.sample_list.setVerticalScrollMode(QtWidgets.QAbstractItemView.ScrollPerPixel)
        self.sample_list.setHorizontalScrollMode(QtWidgets.QAbstractItemView.ScrollPerPixel)
        self.sample_list.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        self.sample_list.setMinimumHeight(60)
        self.sample_list.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.sample_list.setColumnWidth(VISIBLE_COLUMN, 30)
        self.sample_list.setColumnWidth(FILE_COLUMN, 180)
        self.sample_list.setColumnWidth(TEST_TIME_COLUMN, 190)
        self.sample_list.setColumnWidth(ANGLE_COLUMN, 104)
        self.sample_list.setColumnWidth(TENSION_COLUMN, 118)
        self.sample_list.setColumnWidth(SELECTED_PORE_VOLUME_COLUMN, 132)
        self.sample_list.currentCellChanged.connect(self.on_active_cell_changed)
        self.sample_list.itemChanged.connect(self.on_sample_item_changed)
        self.sample_list.itemClicked.connect(self.on_sample_item_clicked)
        self.sample_list.rowHovered.connect(self._on_sample_table_row_hovered)
        self.sample_list.rowMoveRequested.connect(self.move_sample_row)
        self.sample_list.smpFilesDropped.connect(self.add_dropped_files)
        self.sample_list.customContextMenuRequested.connect(self.show_sample_context_menu)
        self.sample_list.horizontalScrollBar().valueChanged.connect(self._position_header_controls)
        self.sample_list.setStyleSheet(
            """
            QTableWidget {
                border: 1px solid #d1d5db;
                background: #ffffff;
            }
            QTableWidget::item:selected {
                background: #e0ecff;
                color: #111827;
            }
            QTableWidget::indicator {
                width: 11px;
                height: 11px;
                border-radius: 6px;
                border: 1px solid #6b7280;
                background: white;
            }
            QTableWidget::indicator:checked {
                border: 1px solid #2563eb;
                background: #2563eb;
            }
            QHeaderView::section {
                background: #f9fafb;
                border: 0;
                border-right: 1px solid #d1d5db;
                border-bottom: 1px solid #d1d5db;
                color: #374151;
                font-weight: 600;
                padding: 4px 24px 4px 6px;
            }
            """
        )
        self.select_all_check.setParent(self.sample_list.frozen_header())
        self.select_all_check.show()
        self.angle_info_button = self._make_header_info_button(
            "进汞接触角",
            (
                "进汞接触角是汞进入孔道时使用的汞-样品接触角。<br><br>"
                "它表示汞对样品表面的非润湿程度。在 Washburn 方程中，它会直接影响压力到孔径的换算，因此修改它会使孔径和孔径分布整体偏移。<br><br>"
                "实际使用中，这个值通常来自代表性表面的接触角测试，或来自实验室针对相似材料验证过的方法参数。<br><br>"
                "当前值优先使用 SMP/MicroActive 方法中保存的值。如果文件里没有有效值，本软件会使用常见的 Micromeritics 推荐默认值：130°。"
            ),
        )
        self.surface_info_button = self._make_header_info_button(
            "表面张力",
            (
                "表面张力是 Washburn 方程中使用的汞表面张力。<br><br>"
                "它描述汞表面的能量状态，会和接触角、压力一起决定计算得到的孔径，因此修改它也会使孔径分布发生偏移。<br><br>"
                "实际使用中，这个值通常来自测试条件下的汞物性数据，或来自实验室/仪器方法中规定的 AutoPore 分析参数。<br><br>"
                "当前值优先使用 SMP/MicroActive 方法中保存的值。如果文件里没有有效值，本软件会使用常见的 Micromeritics 推荐默认值：485 dynes/cm。"
            ),
        )

        sample_panel = QtWidgets.QWidget()
        sample_panel_layout = QtWidgets.QVBoxLayout(sample_panel)
        sample_panel_layout.setContentsMargins(0, 0, 0, 0)
        sample_panel_layout.setSpacing(0)
        sample_panel_layout.addWidget(self.sample_list, 1)
        sample_panel.setMinimumHeight(88)

        self.metrics_stack = QtWidgets.QStackedWidget()
        self._show_empty_metric_table()

        self.left_splitter = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        self.left_splitter.addWidget(sample_panel)
        self.left_splitter.addWidget(self.metrics_stack)
        self.left_splitter.setChildrenCollapsible(False)
        self.left_splitter.setHandleWidth(8)
        self.left_splitter.setSizes([28 + 5 * 30 + 6, 520])
        self.left_splitter.setStyleSheet(
            """
            QSplitter::handle:vertical {
                background: #e5e7eb;
                margin: 2px 0;
            }
            QSplitter::handle:vertical:hover {
                background: #93c5fd;
            }
            """
        )
        side_layout.addWidget(self.left_splitter, 1)
        side_panel.setMinimumWidth(380)

        splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        splitter.addWidget(side_panel)
        splitter.addWidget(self.plot_splitter)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)
        splitter.setSizes([380, 900])

        self.setCentralWidget(splitter)
        self.statusBar().showMessage("导入或拖入 SMP 文件")
        self._sync_select_all_state()
        QtCore.QTimer.singleShot(0, self._position_header_controls)
        QtCore.QTimer.singleShot(AUTO_UPDATE_CHECK_DELAY_MS, self._auto_check_for_updates)

    def _auto_check_for_updates(self) -> None:
        self.check_for_updates(manual=False)

    def check_for_updates(self, _checked: bool = False, *, manual: bool = True) -> None:
        if self._checking_for_updates:
            if manual:
                self.statusBar().showMessage("正在检查软件更新...", 3000)
            return

        self._checking_for_updates = True
        update_button = getattr(self, "update_button", None)
        if update_button is not None:
            update_button.setEnabled(False)
        if manual:
            self.statusBar().showMessage("正在连接更新源检查软件更新...", 3000)

        thread = QtCore.QThread(self)
        worker = UpdateCheckWorker(APP_VERSION, UPDATE_REPOSITORY, manual)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._on_update_check_finished)
        worker.failed.connect(self._on_update_check_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        worker.failed.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._clear_update_check_worker)
        self._update_thread = thread
        self._update_worker = worker
        thread.start()

    def _on_update_check_finished(self, info: UpdateInfo, manual: bool) -> None:
        self._finish_update_check(manual)
        if not info.update_available:
            self._set_update_available_indicator(None)
            message = f"当前已是最新版本 v{info.current_version}"
            if manual:
                QtWidgets.QMessageBox.information(self, "软件更新", message)
            return

        self._set_update_available_indicator(info)
        if not manual:
            self.statusBar().showMessage(f"发现新版本 v{info.latest_version}，点击软件更新左侧图标即可更新。", 8000)
            return

        self._show_update_available_dialog(info)

    def _show_pending_update_dialog(self) -> None:
        info = self._available_update_info
        if info is None:
            self.check_for_updates(manual=True)
            return
        self._show_update_available_dialog(info)

    def _show_update_available_dialog(self, info: UpdateInfo) -> None:
        title = f"发现新版本 v{info.latest_version}"
        download_hint = f"安装包: {info.asset_name}" if info.asset_name else "安装包: 自动选择"
        release_notes = str(info.release_notes or "").strip()
        notes_hint = f"\n\n本次更新内容:\n{release_notes}" if release_notes else "\n\n本次更新内容:\n暂无更新说明。"
        message = (
            f"当前版本: v{info.current_version}\n"
            f"最新版本: v{info.latest_version}\n\n"
            f"来源: DragonScience\n"
            f"{download_hint}"
            f"{notes_hint}\n\n"
            "是否现在下载并重启到新版本？"
        )
        box = QtWidgets.QMessageBox(self)
        box.setIcon(QtWidgets.QMessageBox.Information)
        box.setWindowTitle("软件更新")
        box.setText(title)
        box.setInformativeText(message)
        update_button = box.addButton("更新", QtWidgets.QMessageBox.AcceptRole)
        box.addButton("稍后", QtWidgets.QMessageBox.RejectRole)
        exec_func = getattr(box, "exec", None) or box.exec_
        exec_func()
        if box.clickedButton() == update_button:
            self._download_and_install_update(info)

    def _set_update_available_indicator(self, info: UpdateInfo | None, *, enabled: bool = True) -> None:
        self._available_update_info = info
        button = getattr(self, "update_available_button", None)
        if button is None:
            return
        has_update = info is not None and bool(info.update_available)
        button.setVisible(has_update)
        button.setEnabled(bool(enabled))
        if has_update:
            button.setToolTip(f"发现新版本 v{info.latest_version}，点击更新")
        else:
            button.setToolTip("发现新版本，点击更新")

    def _on_update_check_failed(self, message: str, manual: bool) -> None:
        self._finish_update_check(manual)
        if manual:
            QtWidgets.QMessageBox.warning(self, "软件更新检查失败", message)
        else:
            self.statusBar().showMessage(f"自动检查软件更新失败: {message}", 5000)

    def _finish_update_check(self, manual: bool) -> None:
        self._checking_for_updates = False
        update_button = getattr(self, "update_button", None)
        if update_button is not None:
            update_button.setEnabled(True)
        if not manual:
            self.settings.setValue("updates/last_auto_check_date", datetime.now().date().isoformat())

    def _clear_update_check_worker(self) -> None:
        self._update_thread = None
        self._update_worker = None

    def _download_and_install_update(self, info: UpdateInfo) -> None:
        if self._update_download_thread is not None:
            self.statusBar().showMessage("正在下载软件更新...", 3000)
            return
        if not info.download_url:
            self._open_update_page(info)
            return

        self._show_update_progress(info)
        update_button = getattr(self, "update_button", None)
        if update_button is not None:
            update_button.setEnabled(False)
        self._set_update_available_indicator(info, enabled=False)

        thread = QtCore.QThread(self)
        worker = UpdateDownloadWorker(info)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._on_update_download_progress)
        worker.finished.connect(self._on_update_download_finished)
        worker.failed.connect(self._on_update_download_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        worker.failed.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._clear_update_download_worker)
        self._update_download_thread = thread
        self._update_download_worker = worker
        thread.start()

    def _show_update_progress(self, info: UpdateInfo) -> None:
        dialog = QtWidgets.QProgressDialog(f"正在下载 v{info.latest_version}...", None, 0, 100, self)
        dialog.setWindowTitle("软件更新")
        dialog.setWindowModality(QtCore.Qt.WindowModal)
        dialog.setAutoClose(False)
        dialog.setAutoReset(False)
        dialog.setMinimumDuration(0)
        dialog.setValue(0)
        dialog.setCancelButton(None)
        dialog.setStyleSheet(
            """
            QProgressBar {
                border: 1px solid #9ca3af;
                border-radius: 4px;
                text-align: center;
                background: #f3f4f6;
            }
            QProgressBar::chunk {
                background: #22c55e;
                border-radius: 3px;
            }
            """
        )
        self._update_progress_dialog = dialog
        dialog.show()

    def _on_update_download_progress(self, downloaded: int, total: int) -> None:
        dialog = self._update_progress_dialog
        if dialog is None:
            return
        if total > 0:
            dialog.setRange(0, 100)
            value = max(0, min(100, int(downloaded * 100 / total)))
            dialog.setValue(value)
            dialog.setLabelText(f"正在下载软件更新... {downloaded / 1024 / 1024:.1f} / {total / 1024 / 1024:.1f} MB")
        else:
            dialog.setRange(0, 0)
            dialog.setLabelText(f"正在下载软件更新... {downloaded / 1024 / 1024:.1f} MB")

    def _on_update_download_finished(self, info: UpdateInfo, path: str) -> None:
        dialog = self._update_progress_dialog
        if dialog is not None:
            dialog.setRange(0, 100)
            dialog.setValue(100)
            dialog.setLabelText("下载完成，正在安装更新...")
        self.statusBar().showMessage(f"已下载 v{info.latest_version}，正在安装更新，请稍后重新打开软件。", 3000)
        QtCore.QTimer.singleShot(800, lambda: self._launch_downloaded_update(path))

    def _on_update_download_failed(self, message: str) -> None:
        dialog = self._update_progress_dialog
        if dialog is not None:
            dialog.close()
            self._update_progress_dialog = None
        update_button = getattr(self, "update_button", None)
        if update_button is not None:
            update_button.setEnabled(True)
        if self._available_update_info is not None:
            self._set_update_available_indicator(self._available_update_info, enabled=True)
        QtWidgets.QMessageBox.warning(self, "软件更新失败", message)

    def _clear_update_download_worker(self) -> None:
        self._update_download_thread = None
        self._update_download_worker = None

    def _launch_downloaded_update(self, path: str) -> None:
        try:
            launch_update_and_exit(Path(path))
        except UpdateDownloadError as exc:
            if self._update_progress_dialog is not None:
                self._update_progress_dialog.close()
                self._update_progress_dialog = None
            QtWidgets.QMessageBox.warning(self, "软件更新失败", str(exc))
            update_button = getattr(self, "update_button", None)
            if update_button is not None:
                update_button.setEnabled(True)
            if self._available_update_info is not None:
                self._set_update_available_indicator(self._available_update_info, enabled=True)
            return
        if self._update_progress_dialog is not None:
            self._update_progress_dialog.close()
            self._update_progress_dialog = None
        killer = threading.Timer(5.0, lambda: os._exit(0))
        killer.daemon = True
        killer.start()
        for widget in QtWidgets.QApplication.topLevelWidgets():
            widget.close()
        QtWidgets.QApplication.exit(0)

    def _open_update_page(self, info: UpdateInfo) -> None:
        url = info.download_url or info.release_url
        if not url:
            QtWidgets.QMessageBox.warning(self, "软件更新", "没有可打开的下载链接。")
            return
        QtGui.QDesktopServices.openUrl(QtCore.QUrl(url))

    def _connect_pressure_log_controls(self) -> None:
        controls = self.pressure_plot.getPlotItem().ctrl
        if hasattr(controls, "logXCheck"):
            controls.logXCheck.stateChanged.connect(self.on_pressure_log_changed)

    def _connect_distribution_log_controls(self) -> None:
        controls = self.distribution_plot.getPlotItem().ctrl
        if hasattr(controls, "logXCheck"):
            controls.logXCheck.stateChanged.connect(self.on_distribution_log_changed)

    def dragEnterEvent(self, event) -> None:
        if SampleTableWidget._smp_paths_from_mime_data(event.mimeData()):
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:
        if SampleTableWidget._smp_paths_from_mime_data(event.mimeData()):
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:
        file_paths = SampleTableWidget._smp_paths_from_mime_data(event.mimeData())
        if file_paths:
            event.acceptProposedAction()
            self.add_dropped_files(file_paths)
            return
        super().dropEvent(event)

    @staticmethod
    def _sort_order_from_setting(value):
        value = _qt_enum_int(value)
        if value == _qt_enum_int(QtCore.Qt.DescendingOrder):
            return QtCore.Qt.DescendingOrder
        return QtCore.Qt.AscendingOrder

    def _read_directory_setting(self, key: str) -> Path:
        value = str(self.settings.value(key, "") or "").strip()
        directory = Path(value) if value else Path.cwd()
        return directory if directory.is_dir() else Path.cwd()

    def _write_directory_setting(self, key: str, directory: Path) -> None:
        self.settings.setValue(key, str(directory))

    def open_files(self) -> None:
        existing_paths = [self._result_file_path(result) for result in self.results if self._result_file_path(result)]
        dialog = FileImportDialog(
            self,
            self.import_directory,
            existing_paths=existing_paths,
            available_sort=self._import_available_sort,
        )
        exec_func = getattr(dialog, "exec", dialog.exec_)
        if exec_func() != QtWidgets.QDialog.Accepted:
            self._import_available_sort = dialog.available_sort()
            return
        file_paths = dialog.selected_paths()
        self.import_directory = dialog.current_directory
        self._write_directory_setting("import_directory", self.import_directory)
        self._import_available_sort = dialog.available_sort()
        self.settings.setValue("import_available_sort_column", self._import_available_sort[0])
        self.settings.setValue("import_available_sort_order", _qt_enum_int(self._import_available_sort[1]))
        if not file_paths:
            return

        try:
            self.sync_files(file_paths)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "导入失败", str(exc))

    def export_xls(self) -> None:
        selected_results = [
            result
            for result, visible in zip(self.results, self.visible_results)
            if visible
        ]
        if not selected_results:
            QtWidgets.QMessageBox.information(self, "导出 XLS", "没有选中的 SMP 文件可导出。")
            return

        default_name = f"压汞导出_{len(selected_results)}个样品.xlsx"
        file_path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "导出 XLS",
            str(Path.cwd() / default_name),
            "Excel 工作簿 (*.xlsx)",
        )
        if not file_path:
            return

        try:
            output_path = export_results_xlsx(selected_results, file_path)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "导出失败", str(exc))
            return

        QtWidgets.QMessageBox.information(
            self,
            "导出完成",
            f"已导出 {len(selected_results)} 个样品到：\n{output_path}",
        )

    def load_file(self, file_path: str | Path) -> None:
        self.load_files([file_path])

    def load_files(self, file_paths) -> None:
        self._remove_region()
        self.results = [load_smp(file_path) for file_path in file_paths]
        self.visible_results = [True] * len(self.results)
        self.active_index = 0
        self.result = self.results[0] if self.results else None
        self._refresh_after_file_set()

    def append_files(self, file_paths) -> None:
        existing_paths = {str(result.metadata.get("file_path", "")).lower() for result in self.results}
        new_results = []
        for file_path in file_paths:
            result = load_smp(file_path)
            key = str(result.metadata.get("file_path", "")).lower()
            if key not in existing_paths:
                existing_paths.add(key)
                new_results.append(result)

        if not new_results:
            return

        raw_region = self._current_pressure_region()
        self._remove_region()
        self.results.extend(new_results)
        self.visible_results.extend([True] * len(new_results))
        if self.result is None:
            self.active_index = 0
            self.result = self.results[0]

        self._refresh_after_file_set(raw_region=raw_region, active_index=self.active_index)

    @staticmethod
    def _path_key(path: str | Path) -> str:
        try:
            return str(Path(path).resolve()).lower()
        except OSError:
            return str(path).lower()

    @staticmethod
    def _result_file_path(result) -> str:
        return str(result.metadata.get("file_path") or "")

    def sync_files(self, file_paths: Iterable[str]) -> None:
        existing_by_key = {
            self._path_key(self._result_file_path(result)): result
            for result in self.results
            if self._result_file_path(result)
        }
        new_results = []
        errors = []
        seen_keys: set[str] = set()
        for file_path in file_paths:
            path = Path(file_path)
            if path.suffix.lower() not in SUPPORTED_SMP_SUFFIXES:
                continue
            key = self._path_key(path)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            existing = existing_by_key.get(key)
            if existing is not None:
                new_results.append(existing)
                continue
            try:
                result = load_smp(path)
            except (OSError, ValueError) as exc:
                errors.append(f"{path.name}: {exc}")
                continue
            new_results.append(result)

        if errors:
            QtWidgets.QMessageBox.warning(self, "部分文件未加载", "\n".join(errors))
        if not new_results:
            return

        raw_region = self._current_pressure_region()
        self._remove_region()
        kept_visibility = {id(result): visible for result, visible in zip(self.results, self.visible_results)}
        active_result = self.results[self.active_index] if 0 <= self.active_index < len(self.results) else None
        self.results = new_results
        self.visible_results = [kept_visibility.get(id(result), True) for result in new_results]
        if active_result in new_results:
            self.active_index = new_results.index(active_result)
        else:
            self.active_index = 0
        self.result = self.results[self.active_index] if self.results else None
        self._refresh_after_file_set(raw_region=raw_region, active_index=self.active_index)

    def _refresh_after_file_set(self, raw_region: list[float] | None = None, active_index: int | None = None) -> None:
        self.setWindowTitle(APP_TITLE)
        self._build_metric_tabs(active_index=active_index)
        self._redraw_plots()
        self._add_distribution_selection_items()

        pressure = self._all_pressure_values()
        if pressure.size == 0:
            self.update_metrics()
            return

        if raw_region is None:
            raw_region = self._default_pressure_region_for_active_result(pressure)

        self._add_pressure_region(raw_region, pressure)
        self.update_metrics()

    def add_dropped_files(self, file_paths: list[str]) -> None:
        if not file_paths:
            self.statusBar().showMessage("拖入的文件里没有 SMP 文件。", 4000)
            return
        try:
            before_count = len(self.results)
            self.append_files(file_paths)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "拖拽导入失败", str(exc))
            return

        added_count = len(self.results) - before_count
        if added_count > 0:
            self.statusBar().showMessage(f"已通过拖拽导入 {added_count} 个 SMP 文件。", 5000)
        else:
            self.statusBar().showMessage("拖入的 SMP 文件已在列表中。", 5000)

    def _redraw_plots(self) -> None:
        self.distribution_curve_data = plot_distribution_multi(
            self.distribution_plot,
            self.results,
            self.visible_results,
            self.sample_colors,
        )
        plot_pressure_volume_multi(self.pressure_plot, self.results, self.visible_results, self.sample_colors)
        self._apply_active_sample_curve_selection()

    def _build_metric_tabs(self, active_index: int | None = None) -> None:
        self._build_metric_tabs_with_options(active_index=active_index, preserve_column_widths=False)

    def _build_metric_tabs_with_options(
        self,
        active_index: int | None = None,
        preserve_column_widths: bool = False,
    ) -> None:
        column_widths = self._sample_column_widths() if preserve_column_widths else None
        horizontal_scroll_value = (
            self.sample_list.horizontalScrollBar().value() if preserve_column_widths else None
        )
        self._hovered_sample_row = -1
        if hasattr(self.sample_list, "_hovered_row"):
            self.sample_list._hovered_row = -1
        self._clear_sample_list()
        while self.metrics_stack.count():
            widget = self.metrics_stack.widget(0)
            self.metrics_stack.removeWidget(widget)
            widget.deleteLater()
        self.metric_tables = []
        self.sample_items = []

        self.sample_list.blockSignals(True)
        for index, result in enumerate(self.results):
            table = self._make_metric_table()
            self.metric_tables.append(table)
            self.metrics_stack.addWidget(table)
            self._add_sample_row(index, result)
        if not self.results:
            self._show_empty_metric_table()
        self.sample_list.blockSignals(False)
        if column_widths:
            self._restore_sample_column_widths(column_widths)
        else:
            self._resize_sample_columns_to_contents()
        self._sync_select_all_state()

        if self.results:
            index = 0 if active_index is None else max(0, min(active_index, len(self.results) - 1))
            self.on_active_tab_changed(index)
        if horizontal_scroll_value is not None:
            self._restore_sample_horizontal_scroll(horizontal_scroll_value)

    def _sample_column_widths(self) -> list[int]:
        return [self.sample_list.columnWidth(column) for column in range(self.sample_list.columnCount())]

    def _restore_sample_column_widths(self, widths: list[int]) -> None:
        for column, width in enumerate(widths[: self.sample_list.columnCount()]):
            self.sample_list.setColumnWidth(column, width)
        self._position_header_controls()

    def _restore_sample_horizontal_scroll(self, value: int) -> None:
        scroll_bar = self.sample_list.horizontalScrollBar()

        def restore() -> None:
            scroll_bar.setValue(max(scroll_bar.minimum(), min(int(value), scroll_bar.maximum())))

        restore()
        QtCore.QTimer.singleShot(0, restore)

    def _resize_sample_columns_to_contents(self) -> None:
        self.sample_list.setColumnWidth(VISIBLE_COLUMN, 30)
        self.sample_list.resizeColumnToContents(FILE_COLUMN)
        self.sample_list.setColumnWidth(FILE_COLUMN, max(180, self.sample_list.columnWidth(FILE_COLUMN) + 18))
        self.sample_list.setColumnWidth(TEST_TIME_COLUMN, 190)
        self.sample_list.resizeColumnToContents(ANGLE_COLUMN)
        self.sample_list.setColumnWidth(ANGLE_COLUMN, max(104, self.sample_list.columnWidth(ANGLE_COLUMN) + 24))
        self.sample_list.resizeColumnToContents(TENSION_COLUMN)
        self.sample_list.setColumnWidth(TENSION_COLUMN, max(118, self.sample_list.columnWidth(TENSION_COLUMN) + 24))
        self.sample_list.resizeColumnToContents(SELECTED_PORE_VOLUME_COLUMN)
        self.sample_list.setColumnWidth(
            SELECTED_PORE_VOLUME_COLUMN,
            max(132, self.sample_list.columnWidth(SELECTED_PORE_VOLUME_COLUMN) + 24),
        )
        self._position_header_controls()

    def _make_header_info_button(self, title: str, text: str) -> QtWidgets.QPushButton:
        button = QtWidgets.QPushButton("!", self.sample_list.horizontalHeader())
        button.setFixedSize(15, 15)
        button.setCursor(QtCore.Qt.PointingHandCursor)
        button.setToolTip(title)
        button.clicked.connect(
            lambda checked=False, heading=title, message=text: self.show_header_info(heading, message)
        )
        button.setStyleSheet(
            """
            QPushButton {
                border: 1px solid #cbd5e1;
                border-radius: 8px;
                background: #f8fafc;
                color: #64748b;
                font-weight: 700;
                padding: 0;
            }
            QPushButton:hover {
                background: #f1f5f9;
                border-color: #94a3b8;
            }
            """
        )
        button.show()
        return button

    def _position_header_controls(self, *args) -> None:
        frozen_header = self.sample_list.frozen_header() if hasattr(self.sample_list, "frozen_header") else self.sample_list.horizontalHeader()
        if not frozen_header.isVisible():
            return
        size = self.select_all_check.sizeHint()
        x = frozen_header.sectionViewportPosition(VISIBLE_COLUMN) + (
            frozen_header.sectionSize(VISIBLE_COLUMN) - size.width()
        ) // 2
        y = (frozen_header.height() - size.height()) // 2
        self.select_all_check.setVisible(x + size.width() > 0 and x < frozen_header.width())
        self.select_all_check.setGeometry(x, y, size.width(), size.height())
        if hasattr(self, "angle_info_button"):
            self._position_header_info_button(self.angle_info_button, ANGLE_COLUMN)
        if hasattr(self, "surface_info_button"):
            self._position_header_info_button(self.surface_info_button, TENSION_COLUMN)

    def _position_header_info_button(self, button: QtWidgets.QPushButton, column: int) -> None:
        header = self.sample_list.horizontalHeader()
        size = button.size()
        x = header.sectionViewportPosition(column) + header.sectionSize(column) - size.width() - 8
        y = (header.height() - size.height()) // 2
        visible = x + size.width() > 0 and x < header.width()
        button.setVisible(visible)
        button.setGeometry(x, y, size.width(), size.height())

    def show_header_info(self, title: str, text: str) -> None:
        html = (
            "<div style='white-space: normal; width: 420px;'>"
            f"<b>{title}</b><br><br>{text}"
            "</div>"
        )
        QtWidgets.QToolTip.showText(QtGui.QCursor.pos(), html, self.sample_list.horizontalHeader(), QtCore.QRect(), 16000)

    def show_sample_context_menu(self, position: QtCore.QPoint) -> None:
        index = self.sample_list.rowAt(position.y())
        if index < 0:
            return

        selected_rows = self._selected_sample_rows()
        if index not in selected_rows:
            self.sample_list.clearSelection()
            self.sample_list.selectRow(index)
            self.sample_list.setCurrentCell(index, FILE_COLUMN)
            selected_rows = [index]

        menu = QtWidgets.QMenu(self.sample_list)
        menu.setStyleSheet(
            """
            QMenu {
                background: #ffffff;
                border: 1px solid #d1d5db;
                padding: 3px;
            }
            QMenu::item {
                color: #111827;
                background: transparent;
                padding: 6px 24px 6px 12px;
                margin: 0;
            }
            QMenu::item:selected {
                background: #e0ecff;
            }
            QMenu::indicator {
                width: 0;
                height: 0;
            }
            """
        )
        delete_text = "删除" if len(selected_rows) <= 1 else f"删除选中 {len(selected_rows)} 个样品"
        delete_action = menu.addAction(delete_text)
        global_position = self.sample_list.viewport().mapToGlobal(position)
        selected_action = menu.exec_(global_position) if hasattr(menu, "exec_") else menu.exec(global_position)
        if selected_action == delete_action:
            self.delete_samples(selected_rows)

    def _selected_sample_rows(self) -> list[int]:
        rows = sorted({index.row() for index in self.sample_list.selectionModel().selectedRows()})
        return [row for row in rows if 0 <= row < len(self.results)]

    def delete_sample(self, index: int) -> None:
        self.delete_samples([index])

    def delete_samples(self, indexes: Iterable[int]) -> None:
        rows = sorted({int(index) for index in indexes if 0 <= int(index) < len(self.results)})
        if not rows:
            return

        active_result = self.result
        raw_region = self._current_pressure_region()
        self._remove_region()
        for index in reversed(rows):
            del self.results[index]
            del self.visible_results[index]

        if active_result in self.results:
            target_index = self.results.index(active_result)
        else:
            target_index = min(rows[0], len(self.results) - 1) if self.results else 0
        self.active_index = target_index
        self.result = self.results[target_index] if self.results else None
        self.setWindowTitle(APP_TITLE)

        self._build_metric_tabs()
        if self.results:
            self.on_active_tab_changed(target_index)
        self._redraw_plots()

        if not self.results:
            self.update_metrics()
            return

        self._add_distribution_selection_items()
        pressure = self._all_pressure_values()
        if pressure.size == 0:
            self.update_metrics()
            return

        if raw_region is None:
            raw_region = self._default_pressure_region_for_active_result(pressure)
        else:
            raw_region = self._clamp_pressure_region(raw_region, pressure)

        self._add_pressure_region(raw_region, pressure)
        self.update_metrics()

    def _clear_sample_list(self) -> None:
        self.sample_list.blockSignals(True)
        self.sample_list.setRowCount(0)
        self.sample_list.blockSignals(False)

    def _add_sample_row(self, index: int, result) -> None:
        self.sample_list.insertRow(index)

        visible_item = QtWidgets.QTableWidgetItem()
        visible_item.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable | QtCore.Qt.ItemIsUserCheckable)
        visible_item.setCheckState(QtCore.Qt.Checked if self.visible_results[index] else QtCore.Qt.Unchecked)
        visible_item.setTextAlignment(QtCore.Qt.AlignCenter)
        self.sample_list.setItem(index, VISIBLE_COLUMN, visible_item)

        file_item = QtWidgets.QTableWidgetItem(self._sample_row_label(result))
        file_item.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable)
        file_item.setToolTip(str(result.metadata.get("file_path", "")))
        file_item.setForeground(QtGui.QBrush(QtGui.QColor("#111827")))
        self.sample_list.setItem(index, FILE_COLUMN, file_item)

        test_time_item = QtWidgets.QTableWidgetItem(self._test_time_text(result))
        test_time_item.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable)
        test_time_item.setToolTip("来自 SMP 创建时间的测试时间")
        test_time_item.setForeground(QtGui.QBrush(QtGui.QColor("#111827")))
        self.sample_list.setItem(index, TEST_TIME_COLUMN, test_time_item)

        angle_item = self._make_parameter_item(result, "adv_contact_angle_deg", "adv_contact_angle_is_override", "deg")
        tension_item = self._make_parameter_item(
            result,
            "surface_tension_dynes_cm",
            "surface_tension_is_override",
            "dynes/cm",
        )
        self.sample_list.setItem(index, ANGLE_COLUMN, angle_item)
        self.sample_list.setItem(index, TENSION_COLUMN, tension_item)
        self.sample_list.setItem(index, SELECTED_PORE_VOLUME_COLUMN, self._make_selected_pore_volume_item(None))

        self.sample_items.append(visible_item)

    def _sample_row_label(self, result) -> str:
        return self._display_text(result.metadata.get("file_name"))

    def _test_time_text(self, result) -> str:
        return self._display_text(result.metadata.get("created"))

    def _make_parameter_item(self, result, value_key: str, override_key: str, suffix: str) -> QtWidgets.QTableWidgetItem:
        item = QtWidgets.QTableWidgetItem(self._format_parameter_value(result.metadata.get(value_key), suffix))
        item.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable | QtCore.Qt.ItemIsEditable)
        item.setTextAlignment(QtCore.Qt.AlignCenter)
        item.setData(QtCore.Qt.UserRole, value_key)
        item.setForeground(
            QtGui.QBrush(QtGui.QColor("#111827" if result.metadata.get(override_key) else "#9ca3af"))
        )
        return item

    def _make_selected_pore_volume_item(self, value) -> QtWidgets.QTableWidgetItem:
        item = QtWidgets.QTableWidgetItem(self._format_selected_pore_volume(value))
        item.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable)
        item.setTextAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        item.setForeground(QtGui.QBrush(QtGui.QColor("#047857")))
        return item

    def _set_selected_pore_volume_cell(self, row: int, value) -> None:
        if not (0 <= row < self.sample_list.rowCount()):
            return
        item = self.sample_list.item(row, SELECTED_PORE_VOLUME_COLUMN)
        if item is None:
            item = self._make_selected_pore_volume_item(value)
            self.sample_list.setItem(row, SELECTED_PORE_VOLUME_COLUMN, item)
            return
        item.setText(self._format_selected_pore_volume(value))

    @staticmethod
    def _format_selected_pore_volume(value) -> str:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return ""
        if not np.isfinite(number):
            return ""
        return f"{number:.6g}"

    def _make_metric_table(self) -> QtWidgets.QTableWidget:
        table = QtWidgets.QTableWidget(0, 2)
        table.setHorizontalHeaderLabels(["参数", "数值"])
        table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.Stretch)
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        return table

    def _show_empty_metric_table(self) -> None:
        table = self._make_metric_table()
        table.setRowCount(0)
        self.metrics_stack.addWidget(table)

    def on_active_tab_changed(self, index: int) -> None:
        if 0 <= index < len(self.results):
            self.active_index = index
            self.result = self.results[index]
            self.metrics_stack.setCurrentIndex(index)
            if self.sample_list.currentRow() != index:
                self.sample_list.blockSignals(True)
                self.sample_list.setCurrentCell(index, FILE_COLUMN)
                self.sample_list.blockSignals(False)
            self.update_metrics()
            self._apply_active_sample_curve_selection()

    def on_sample_header_clicked(self, section: int) -> None:
        if len(self.results) < 2:
            return
        if section == TEST_TIME_COLUMN:
            self.test_time_sort_ascending = not self.test_time_sort_ascending
            self.sort_samples_by_test_time(self.test_time_sort_ascending)
        elif section == SELECTED_PORE_VOLUME_COLUMN:
            self.selected_pore_volume_sort_ascending = not self.selected_pore_volume_sort_ascending
            self.sort_samples_by_selected_pore_volume(self.selected_pore_volume_sort_ascending)

    def sort_samples_by_test_time(self, ascending: bool) -> None:
        self._sort_samples(lambda result: self._test_time_sort_key(result), ascending)

    def sort_samples_by_selected_pore_volume(self, ascending: bool) -> None:
        self._sort_samples(lambda result: self._selected_pore_volume_sort_key(result), ascending)

    def _sort_samples(self, key_func, ascending: bool) -> None:
        active_result = self.result
        pairs = []
        for index, result in enumerate(self.results):
            visible = self.visible_results[index] if index < len(self.visible_results) else True
            color = self.sample_colors[index] if index < len(self.sample_colors) else DEFAULT_COLORS[index % len(DEFAULT_COLORS)]
            pairs.append((result, visible, color))
        pairs.sort(key=lambda pair: key_func(pair[0]), reverse=not ascending)
        self.results = [pair[0] for pair in pairs]
        self.visible_results = [pair[1] for pair in pairs]
        self.sample_colors = [pair[2] for pair in pairs] + self.sample_colors[len(pairs):]

        active_index = 0
        for index, result in enumerate(self.results):
            if result is active_result:
                active_index = index
                break
        self.active_index = active_index
        self.result = self.results[active_index] if self.results else None

        self._build_metric_tabs_with_options(active_index=active_index, preserve_column_widths=True)
        self._refresh_visibility_dependent_ui()

    def move_sample_row(self, source_index: int, insert_index: int) -> None:
        if len(self.results) < 2 or not (0 <= source_index < len(self.results)):
            return

        insert_index = max(0, min(int(insert_index), len(self.results)))
        if insert_index in (source_index, source_index + 1):
            return

        active_result = self.result
        moved_result = self.results.pop(source_index)
        moved_visible = self.visible_results.pop(source_index)
        moved_color = self.sample_colors.pop(source_index) if source_index < len(self.sample_colors) else None

        if insert_index > source_index:
            insert_index -= 1

        self.results.insert(insert_index, moved_result)
        self.visible_results.insert(insert_index, moved_visible)
        if moved_color is not None:
            self.sample_colors.insert(insert_index, moved_color)

        active_index = insert_index
        if active_result is not None:
            for index, result in enumerate(self.results):
                if result is active_result:
                    active_index = index
                    break

        self.active_index = active_index
        self.result = self.results[active_index] if self.results else None
        self._build_metric_tabs_with_options(active_index=active_index, preserve_column_widths=True)
        self._refresh_visibility_dependent_ui()

    @staticmethod
    def _test_time_sort_key(result) -> str:
        return str(result.metadata.get("created") or "")

    def _selected_pore_volume_sort_key(self, result) -> float:
        region = self._current_pressure_region()
        if region is None:
            return 0.0
        try:
            metrics = metrics_for_pressure_range(result, region[0], region[1])
            value = float(metrics.pore_volume)
        except Exception:
            return 0.0
        return value if np.isfinite(value) else 0.0

    def on_active_cell_changed(self, current_row: int, current_column: int, previous_row: int, previous_column: int) -> None:
        self.on_active_tab_changed(current_row)

    def _select_sample_from_curve(self, row: int) -> None:
        if row < 0 or row >= len(self.results):
            return
        current_column = self.sample_list.currentColumn()
        if current_column < 0:
            current_column = FILE_COLUMN
        self.sample_list.setCurrentCell(int(row), int(current_column))
        self.sample_list.selectRow(int(row))
        try:
            item = self.sample_list.item(int(row), FILE_COLUMN)
            if item is not None:
                self.sample_list.scrollToItem(item, QtWidgets.QAbstractItemView.PositionAtCenter)
        except Exception:
            pass
        self.statusBar().showMessage(f"已切换到样品：{self._sample_row_label(self.results[int(row)])}", 2200)

    def _sample_hover_plots(self) -> tuple[object, ...]:
        return (self.distribution_plot, self.pressure_plot)

    def _apply_active_sample_curve_selection(self) -> None:
        sample_index = self.active_index if 0 <= self.active_index < len(self.results) else None
        set_sample_curve_selected_plots(sample_index, *self._sample_hover_plots())

    def _on_sample_table_row_hovered(self, row: int) -> None:
        sample_index = int(row) if 0 <= int(row) < len(self.results) else None
        self._set_hovered_sample_row(sample_index)
        set_sample_curve_hover_plots(sample_index, *self._sample_hover_plots())

    def _set_hovered_sample_row(self, row: int | None) -> None:
        target = int(row) if row is not None and 0 <= int(row) < len(self.results) else -1
        if target == self._hovered_sample_row:
            return
        previous = self._hovered_sample_row
        self._hovered_sample_row = target
        self._apply_sample_row_hover(previous, False)
        self._apply_sample_row_hover(target, True)

    def _apply_sample_row_hover(self, row: int, hovered: bool) -> None:
        if row < 0 or row >= self.sample_list.rowCount():
            return

        was_updating = self._updating_sample_checks
        previous_block = self.sample_list.blockSignals(True)
        self._updating_sample_checks = True
        try:
            for column in range(self.sample_list.columnCount()):
                if column == VISIBLE_COLUMN:
                    continue
                item = self.sample_list.item(row, column)
                if item is None:
                    continue
                if hovered:
                    if not isinstance(item.data(HOVER_BASE_FONT_ROLE), QtGui.QFont):
                        item.setData(HOVER_BASE_FONT_ROLE, QtGui.QFont(item.font()))
                    if not isinstance(item.data(HOVER_BASE_FOREGROUND_ROLE), QtGui.QBrush):
                        item.setData(HOVER_BASE_FOREGROUND_ROLE, QtGui.QBrush(item.foreground()))
                    font = QtGui.QFont(item.font())
                    font.setBold(True)
                    item.setFont(font)
                    item.setForeground(QtGui.QBrush(QtGui.QColor("#111827")))
                    continue

                base_font = item.data(HOVER_BASE_FONT_ROLE)
                base_foreground = item.data(HOVER_BASE_FOREGROUND_ROLE)
                if isinstance(base_font, QtGui.QFont):
                    item.setFont(QtGui.QFont(base_font))
                    item.setData(HOVER_BASE_FONT_ROLE, None)
                if isinstance(base_foreground, QtGui.QBrush):
                    item.setForeground(QtGui.QBrush(base_foreground))
                    item.setData(HOVER_BASE_FOREGROUND_ROLE, None)
        finally:
            self._updating_sample_checks = was_updating
            self.sample_list.blockSignals(previous_block)
        self.sample_list.viewport().update()

    def on_sample_item_clicked(self, item: QtWidgets.QTableWidgetItem) -> None:
        if item.column() in (ANGLE_COLUMN, TENSION_COLUMN):
            self.sample_list.editItem(item)

    def on_sample_item_changed(self, item: QtWidgets.QTableWidgetItem) -> None:
        if self._updating_sample_checks:
            return
        index = item.row()
        if item.column() == VISIBLE_COLUMN:
            checked = _check_state_value(item.checkState()) == _check_state_value(QtCore.Qt.Checked)
            self.on_visibility_changed(index, checked)
        elif item.column() in (ANGLE_COLUMN, TENSION_COLUMN):
            self.on_sample_parameter_changed(index, item.column())

    def on_visibility_changed(self, index: int, checked: bool) -> None:
        if not (0 <= index < len(self.visible_results)):
            return
        self.visible_results[index] = checked
        self._sync_select_all_state()
        self._refresh_visibility_dependent_ui()

    def on_sample_parameter_changed(self, index: int, column: int) -> None:
        if not (0 <= index < len(self.results)):
            return

        try:
            theta = self._parameter_value_from_cell(index, ANGLE_COLUMN)
            gamma = self._parameter_value_from_cell(index, TENSION_COLUMN)
            self._validate_calculation_parameters(theta, gamma)
        except ValueError as exc:
            self._restore_parameter_cells(index)
            self.statusBar().showMessage(str(exc), 5000)
            return

        raw_smp = self.results[index].raw_smp
        if raw_smp is None:
            self.statusBar().showMessage("无法重新计算：原始 SMP 数据不可用。", 5000)
            return

        theta_override = None if np.isclose(theta, raw_smp.adv_contact_angle_deg) else theta
        gamma_override = None if np.isclose(gamma, raw_smp.surface_tension_dynes_cm) else gamma

        try:
            updated = calculate_microactive(
                raw_smp,
                adv_contact_angle_deg=theta_override,
                surface_tension_dynes_cm=gamma_override,
            )
        except Exception as exc:
            self._restore_parameter_cells(index)
            self.statusBar().showMessage(f"重新计算失败：{exc}", 5000)
            return

        self.results[index] = updated
        if index == self.active_index:
            self.result = updated
        self._restore_parameter_cells(index)
        self._refresh_visibility_dependent_ui()

    def _parameter_value_from_cell(self, row: int, column: int) -> float:
        item = self.sample_list.item(row, column)
        if item is None:
            raise ValueError("缺少计算参数。")
        return self._parse_parameter_text(item.text())

    @staticmethod
    def _parse_parameter_text(text: str) -> float:
        normalized = (
            str(text)
            .strip()
            .lower()
            .replace(",", "")
            .replace("°", "")
            .replace("degrees", "")
            .replace("degree", "")
            .replace("deg", "")
            .replace("dynes/cm", "")
            .replace("dyne/cm", "")
        )
        if not normalized:
            raise ValueError("计算参数不能为空。")
        try:
            return float(normalized)
        except ValueError as exc:
            raise ValueError("计算参数必须是数字。") from exc

    @staticmethod
    def _validate_calculation_parameters(theta: float, gamma: float) -> None:
        if not 90.0 < theta < 180.0:
            raise ValueError("进汞接触角必须在 90 到 180 度之间。")
        if not 100.0 < gamma < 600.0:
            raise ValueError("表面张力必须在 100 到 600 dynes/cm 之间。")

    def _restore_parameter_cells(self, index: int) -> None:
        if not (0 <= index < len(self.results)):
            return
        result = self.results[index]
        self._updating_sample_checks = True
        self._set_parameter_cell(
            index,
            ANGLE_COLUMN,
            result.metadata.get("adv_contact_angle_deg"),
            "deg",
            bool(result.metadata.get("adv_contact_angle_is_override")),
        )
        self._set_parameter_cell(
            index,
            TENSION_COLUMN,
            result.metadata.get("surface_tension_dynes_cm"),
            "dynes/cm",
            bool(result.metadata.get("surface_tension_is_override")),
        )
        self._updating_sample_checks = False

    def _set_parameter_cell(self, row: int, column: int, value, suffix: str, is_override: bool) -> None:
        item = self.sample_list.item(row, column)
        if item is None:
            return
        item.setText(self._format_parameter_value(value, suffix))
        item.setForeground(QtGui.QBrush(QtGui.QColor("#111827" if is_override else "#9ca3af")))

    def on_select_all_changed(self, state: int) -> None:
        if self._updating_sample_checks or not self.visible_results:
            return
        state_value = _check_state_value(state)
        if state_value == _check_state_value(QtCore.Qt.PartiallyChecked):
            return
        checked = state_value == _check_state_value(QtCore.Qt.Checked)
        self.visible_results = [checked] * len(self.visible_results)

        self.sample_list.blockSignals(True)
        for item in self.sample_items:
            item.setCheckState(QtCore.Qt.Checked if checked else QtCore.Qt.Unchecked)
        self.sample_list.blockSignals(False)

        self._refresh_visibility_dependent_ui()

    def _sync_select_all_state(self) -> None:
        if not self.visible_results:
            state = QtCore.Qt.Unchecked
        elif all(self.visible_results):
            state = QtCore.Qt.Checked
        elif any(self.visible_results):
            state = QtCore.Qt.PartiallyChecked
        else:
            state = QtCore.Qt.Unchecked

        self._updating_sample_checks = True
        self.select_all_check.setEnabled(bool(self.visible_results))
        self.select_all_check.setCheckState(state)
        self._updating_sample_checks = False

    def _refresh_visibility_dependent_ui(self) -> None:
        raw_region = None
        if self.region is not None:
            raw_region = self._region_to_pressure_values(*self.region.getRegion())
        self._remove_region()
        self._redraw_plots()
        self._add_distribution_selection_items()
        if raw_region is not None:
            pressure = self._all_pressure_values()
            if pressure.size:
                self._add_pressure_region(raw_region, pressure)
        self.update_metrics()

    def _all_pressure_values(self) -> np.ndarray:
        arrays = [
            result.pressure[np.isfinite(result.pressure)]
            for result in self.results
            if result.pressure.size
        ]
        return np.concatenate(arrays) if arrays else np.array([])

    @staticmethod
    def _default_pressure_region(pressure: np.ndarray) -> list[float]:
        lo = float(np.nanmin(pressure))
        hi = float(np.nanmax(pressure))
        span = hi - lo
        return [lo + span * 0.25, lo + span * 0.55]

    def _default_pressure_region_for_active_result(self, pressure: np.ndarray) -> list[float]:
        default_from_diameter = self._default_pressure_region_from_diameter_range()
        if default_from_diameter is not None:
            return self._clamp_pressure_region(default_from_diameter, pressure)
        return self._default_pressure_region(pressure)

    def _default_pressure_region_from_diameter_range(self) -> list[float] | None:
        if self.result is None:
            return None
        bounds = self._active_diameter_bounds()
        if bounds is None:
            return None
        default_min, default_max = sorted(DEFAULT_DISTRIBUTION_DIAMETER_REGION)
        bounds_min, bounds_max = sorted((float(bounds[0]), float(bounds[1])))
        diameter_min = max(bounds_min, float(default_min))
        diameter_max = min(bounds_max, float(default_max))
        if not (
            np.isfinite(diameter_min)
            and np.isfinite(diameter_max)
            and diameter_min < diameter_max
        ):
            return None
        return self._pressure_from_diameter_range(self.result, diameter_min, diameter_max)

    def _clamp_pressure_region(self, raw_region: list[float], pressure: np.ndarray) -> list[float]:
        lo = float(np.nanmin(pressure))
        hi = float(np.nanmax(pressure))
        region_lo, region_hi = sorted((float(raw_region[0]), float(raw_region[1])))
        region_lo = max(lo, min(region_lo, hi))
        region_hi = max(lo, min(region_hi, hi))
        if region_lo < region_hi:
            return [region_lo, region_hi]
        return self._default_pressure_region(pressure)

    def _make_selection_region(
        self,
        values,
        bounds=None,
        movable: bool = True,
        *,
        line_color: str = REGION_LINE_COLOR,
        hover_line_color: str = REGION_LINE_HOVER_COLOR,
        fill_color: tuple[int, int, int, int] = REGION_FILL_COLOR,
        hover_fill_color: tuple[int, int, int, int] = REGION_FILL_HOVER_COLOR,
    ):
        region = pg.LinearRegionItem(
            values,
            bounds=bounds,
            movable=movable,
            brush=pg.mkBrush(*fill_color),
            hoverBrush=pg.mkBrush(*hover_fill_color),
            pen=_region_pen(line_color),
            hoverPen=_region_pen(hover_line_color),
            swapMode="block",
        )
        self._style_selection_region(region, line_color=line_color, hover_line_color=hover_line_color)
        return region

    @staticmethod
    def _style_selection_region(
        region,
        *,
        line_color: str = REGION_LINE_COLOR,
        hover_line_color: str = REGION_LINE_HOVER_COLOR,
    ) -> None:
        for line in getattr(region, "lines", []):
            line.setPen(_region_pen(line_color))
            line.setHoverPen(_region_pen(hover_line_color))
            line.setCursor(QtCore.Qt.SizeHorCursor)

    def _add_pressure_region(self, raw_region: list[float], pressure: np.ndarray) -> None:
        if pressure.size == 0:
            return
        raw_region = self._clamp_pressure_region(raw_region, pressure)
        self.pressure_region_is_log = self._pressure_log_enabled()
        self.region = self._make_selection_region(
            self._pressure_to_region_values(raw_region[0], raw_region[1]),
            bounds=self._pressure_to_region_values(np.nanmin(pressure), np.nanmax(pressure)),
            movable=True,
        )
        self.region.sigRegionChanged.connect(self.on_pressure_region_changed)
        self.pressure_plot.addItem(self.region, ignoreBounds=True)

    def _remove_region(self) -> None:
        if self.region is None:
            self._remove_distribution_selection_items()
            return
        try:
            self.region.sigRegionChanged.disconnect(self.on_pressure_region_changed)
        except (RuntimeError, TypeError):
            pass
        try:
            self.pressure_plot.removeItem(self.region)
        except RuntimeError:
            pass
        self.region = None
        self.pressure_region_is_log = False
        self._remove_distribution_selection_items()

    def on_pressure_region_changed(self) -> None:
        if self._syncing_region_changes:
            return
        self.queue_metrics_update()

    def on_pressure_log_changed(self) -> None:
        if not self.results or self.region is None:
            return
        raw_lo, raw_hi = self._region_to_pressure_values(*self.region.getRegion())
        self.pressure_region_is_log = self._pressure_log_enabled()
        pressure = self._all_pressure_values()
        self._syncing_region_changes = True
        try:
            if pressure.size:
                self.region.setBounds(self._pressure_to_region_values(np.nanmin(pressure), np.nanmax(pressure)))
            self.region.setRegion(self._pressure_to_region_values(raw_lo, raw_hi))
        finally:
            self._syncing_region_changes = False
        self.queue_metrics_update()

    def _pressure_log_enabled(self) -> bool:
        controls = self.pressure_plot.getPlotItem().ctrl
        return bool(getattr(controls, "logXCheck").isChecked()) if hasattr(controls, "logXCheck") else False

    def _pressure_to_region_values(self, pressure_min: float, pressure_max: float) -> list[float]:
        lo, hi = sorted((float(pressure_min), float(pressure_max)))
        if self._pressure_log_enabled() and lo > 0 and hi > 0:
            return [float(np.log10(lo)), float(np.log10(hi))]
        return [lo, hi]

    def _region_to_pressure_values(self, region_min: float, region_max: float) -> list[float]:
        lo, hi = sorted((float(region_min), float(region_max)))
        if self.pressure_region_is_log:
            return [float(10.0**lo), float(10.0**hi)]
        return [lo, hi]

    def _current_pressure_region(self) -> list[float] | None:
        if self.region is None:
            return None
        try:
            return self._region_to_pressure_values(*self.region.getRegion())
        except RuntimeError:
            return None

    def _add_distribution_selection_items(self) -> None:
        self.distribution_region_is_log = self._distribution_log_enabled()
        self.distribution_region = self._make_selection_region(
            [0.0, 0.0],
            movable=True,
            line_color=DISTRIBUTION_REGION_LINE_COLOR,
            hover_line_color=DISTRIBUTION_REGION_LINE_HOVER_COLOR,
            fill_color=DISTRIBUTION_REGION_FILL_COLOR,
            hover_fill_color=DISTRIBUTION_REGION_FILL_HOVER_COLOR,
        )
        self.distribution_region.setVisible(False)
        self.distribution_region.sigRegionChanged.connect(self.on_distribution_region_changed)
        self.distribution_region.sigRegionChanged.connect(self._on_distribution_region_label_changed)
        finished_signal = getattr(self.distribution_region, "sigRegionChangeFinished", None)
        if finished_signal is not None:
            finished_signal.connect(self._on_distribution_region_change_finished)
        self.distribution_plot.addItem(self.distribution_region, ignoreBounds=True)
        self.distribution_region_labels = [
            RegionEndpointLabel(0, self._begin_distribution_region_endpoint_edit),
            RegionEndpointLabel(1, self._begin_distribution_region_endpoint_edit),
        ]
        for label in self.distribution_region_labels:
            label.hide()
            self.distribution_plot.addItem(label, ignoreBounds=True)
        self.distribution_selected_curve = self.distribution_plot.plot(
            [],
            [],
            pen=pg.mkPen(DISTRIBUTION_REGION_LINE_COLOR, width=3),
        )
        self.distribution_selected_points = self.distribution_plot.plot(
            [],
            [],
            pen=None,
            symbol="o",
            symbolSize=7,
            symbolPen=pg.mkPen(DISTRIBUTION_REGION_LINE_COLOR, width=1),
            symbolBrush=pg.mkBrush("#dcfce7"),
        )

    def _remove_distribution_selection_items(self) -> None:
        self.distribution_region_label_timer.stop()
        self._hide_distribution_region_editor()
        if self.distribution_region is not None:
            try:
                self.distribution_region.sigRegionChanged.disconnect(self.on_distribution_region_changed)
            except (RuntimeError, TypeError):
                pass
            try:
                self.distribution_region.sigRegionChanged.disconnect(self._on_distribution_region_label_changed)
            except (RuntimeError, TypeError):
                pass
            finished_signal = getattr(self.distribution_region, "sigRegionChangeFinished", None)
            if finished_signal is not None:
                try:
                    finished_signal.disconnect(self._on_distribution_region_change_finished)
                except (RuntimeError, TypeError):
                    pass
            try:
                self.distribution_plot.removeItem(self.distribution_region)
            except RuntimeError:
                pass
            self.distribution_region = None
            self.distribution_region_is_log = False
        for label in self.distribution_region_labels:
            try:
                self.distribution_plot.removeItem(label)
            except RuntimeError:
                pass
        self.distribution_region_labels = []
        if self.distribution_selected_curve is not None:
            try:
                self.distribution_plot.removeItem(self.distribution_selected_curve)
            except RuntimeError:
                pass
            self.distribution_selected_curve = None
        if self.distribution_selected_points is not None:
            try:
                self.distribution_plot.removeItem(self.distribution_selected_points)
            except RuntimeError:
                pass
            self.distribution_selected_points = None

    def on_distribution_region_changed(self) -> None:
        if self._syncing_region_changes or self.distribution_region is None:
            return
        if self.result is None or self.region is None:
            return
        if self.active_index >= len(self.visible_results) or not self.visible_results[self.active_index]:
            return

        try:
            diameter_min, diameter_max = self._region_to_diameter_values(*self.distribution_region.getRegion())
        except RuntimeError:
            return

        pressure_region = self._pressure_from_diameter_range(self.result, diameter_min, diameter_max)
        if pressure_region is None:
            return

        pressure = self._all_pressure_values()
        if pressure.size:
            pressure_region = self._clamp_pressure_region(pressure_region, pressure)

        self.pressure_region_is_log = self._pressure_log_enabled()
        self._syncing_region_changes = True
        try:
            if pressure.size:
                self.region.setBounds(self._pressure_to_region_values(np.nanmin(pressure), np.nanmax(pressure)))
            self.region.setRegion(self._pressure_to_region_values(pressure_region[0], pressure_region[1]))
        finally:
            self._syncing_region_changes = False
        self.queue_metrics_update()

    def _on_distribution_region_label_changed(self) -> None:
        self._cancel_distribution_region_editor_if_unchanged()
        self._show_distribution_region_labels(auto_hide=True)

    def _on_distribution_region_change_finished(self) -> None:
        self._show_distribution_region_labels(auto_hide=True)

    def _on_distribution_view_range_changed(self, *_args) -> None:
        if any(self._safe_item_is_visible(label) for label in self.distribution_region_labels):
            self._update_distribution_region_labels()

    def _ignore_distribution_coordinate_click(self, scene_pos: QtCore.QPointF) -> bool:
        if self._editing_distribution_endpoint:
            return True
        editor = self.distribution_region_editor
        if editor is not None:
            try:
                plot_pos = self.distribution_plot.mapFromScene(scene_pos)
                if (not editor.isHidden()) and editor.geometry().adjusted(-3, -3, 3, 3).contains(plot_pos):
                    return True
            except RuntimeError:
                pass
        for label in self.distribution_region_labels:
            try:
                if label.isVisible() and label.sceneBoundingRect().adjusted(-4.0, -4.0, 4.0, 4.0).contains(scene_pos):
                    return True
            except RuntimeError:
                continue
        return False

    @staticmethod
    def _safe_item_is_visible(item) -> bool:
        try:
            return bool(item.isVisible())
        except RuntimeError:
            return False

    def _show_distribution_region_labels(self, *, auto_hide: bool) -> None:
        if not self._update_distribution_region_labels():
            return
        editing_index = self._editing_distribution_endpoint_index
        editor_visible = self._distribution_region_editor_visible()
        for index, label in enumerate(self.distribution_region_labels):
            if editor_visible and editing_index == index:
                label.hide()
            else:
                label.show()
        if auto_hide and not self._editing_distribution_endpoint:
            self.distribution_region_label_timer.start()

    def _hide_distribution_region_labels(self) -> None:
        for label in self.distribution_region_labels:
            try:
                label.hide()
            except RuntimeError:
                pass

    def _distribution_region_editor_visible(self) -> bool:
        editor = self.distribution_region_editor
        if editor is None:
            return False
        try:
            return not bool(editor.isHidden())
        except RuntimeError:
            return False

    def _update_distribution_region_labels(self) -> bool:
        if (
            self.distribution_region is None
            or len(self.distribution_region_labels) < 2
            or not self.distribution_region.isVisible()
        ):
            self._hide_distribution_region_labels()
            return False

        try:
            region_values = sorted(float(value) for value in self.distribution_region.getRegion())
            diameter_values = self._region_to_diameter_values(region_values[0], region_values[1])
            view_range = self.distribution_plot.getPlotItem().getViewBox().viewRange()
        except RuntimeError:
            self._hide_distribution_region_labels()
            return False

        if not view_range or len(view_range) != 2:
            self._hide_distribution_region_labels()
            return False
        (x_min, x_max), (y_min, y_max) = view_range
        if not np.isfinite(y_min) or not np.isfinite(y_max):
            self._hide_distribution_region_labels()
            return False

        y_position = min(float(y_min), float(y_max))
        for label_index, (label, x_position, diameter) in enumerate(
            zip(self.distribution_region_labels, region_values, diameter_values)
        ):
            label.setText(self._format_axis_number(diameter))
            anchor_x, pixel_offset = self._distribution_region_label_anchor(
                label,
                label_index,
                float(x_position),
                (float(x_min), float(x_max)),
            )
            label.setAnchor((anchor_x, 1.0))
            label_x = self._view_x_with_pixel_offset(float(x_position), pixel_offset)
            label.setPos(label_x, y_position)
            if self._distribution_region_editor_visible() and self._editing_distribution_endpoint_index == label_index:
                if not self._distribution_endpoint_editor_dirty:
                    self._set_distribution_region_editor_text(self._format_axis_number(diameter))
                self._position_distribution_region_editor(label_x, y_position, anchor_x, label)
        return True

    def _distribution_region_label_anchor(
        self,
        label,
        label_index: int,
        x_position: float,
        x_range: tuple[float, float],
    ) -> tuple[float, float]:
        view_box = self.distribution_plot.getPlotItem().getViewBox()
        try:
            scene_rect = view_box.sceneBoundingRect()
            scene_x = float(view_box.mapViewToScene(QtCore.QPointF(float(x_position), 0.0)).x())
            label_width = max(28.0, float(label.boundingRect().width()))
        except Exception:
            x_min, x_max = sorted(x_range)
            span = max(abs(x_max - x_min), 1e-12)
            at_left_edge = float(x_position) - x_min < span * 0.08
            at_right_edge = x_max - float(x_position) < span * 0.08
            if int(label_index) <= 0:
                return (0.0, 5.0) if at_left_edge else (1.0, -5.0)
            return (1.0, -5.0) if at_right_edge else (0.0, 5.0)

        margin = 6.0
        if int(label_index) <= 0:
            if scene_x - label_width - margin < scene_rect.left():
                return 0.0, 5.0
            return 1.0, -5.0
        if scene_x + label_width + margin > scene_rect.right():
            return 1.0, -5.0
        return 0.0, 5.0

    def _view_x_with_pixel_offset(self, x_position: float, pixel_offset: float) -> float:
        view_box = self.distribution_plot.getPlotItem().getViewBox()
        try:
            scene_pos = view_box.mapViewToScene(QtCore.QPointF(float(x_position), 0.0))
            shifted = QtCore.QPointF(scene_pos.x() + float(pixel_offset), scene_pos.y())
            return float(view_box.mapSceneToView(shifted).x())
        except Exception:
            return float(x_position)

    def _ensure_distribution_region_editor(self) -> RegionEndpointLineEdit:
        if self.distribution_region_editor is not None:
            return self.distribution_region_editor

        editor = RegionEndpointLineEdit(self.distribution_plot)
        editor.cancel_requested = self._cancel_distribution_region_endpoint_edit
        editor.textEdited.connect(self._mark_distribution_region_editor_dirty)
        editor.editingFinished.connect(self._finish_distribution_region_endpoint_edit)
        editor.hide()
        self.distribution_region_editor = editor
        self.distribution_region_editor_proxy = None
        return editor

    def _mark_distribution_region_editor_dirty(self, _text: str) -> None:
        self._distribution_endpoint_editor_dirty = True

    def _set_distribution_region_editor_text(self, text: str) -> None:
        editor = self.distribution_region_editor
        if editor is None:
            return
        was_blocked = editor.blockSignals(True)
        try:
            editor.setText(text)
        finally:
            editor.blockSignals(was_blocked)

    def _position_distribution_region_editor(
        self,
        x_position: float,
        y_position: float,
        anchor_x: float,
        label: pg.TextItem | None = None,
    ) -> None:
        editor = self.distribution_region_editor
        if editor is None:
            return
        try:
            if label is not None:
                rect = label.sceneBoundingRect()
                top_left = self.distribution_plot.mapFromScene(rect.topLeft())
                bottom_right = self.distribution_plot.mapFromScene(rect.bottomRight())
                if hasattr(top_left, "toPoint"):
                    top_left = top_left.toPoint()
                if hasattr(bottom_right, "toPoint"):
                    bottom_right = bottom_right.toPoint()
                x1, y1 = int(top_left.x()), int(top_left.y())
                x2, y2 = int(bottom_right.x()), int(bottom_right.y())
                left = min(x1, x2)
                top = min(y1, y2)
                width = max(28, abs(x2 - x1))
                height = max(20, abs(y2 - y1))
                editor.setFixedSize(width, height)
            else:
                view_box = self.distribution_plot.getPlotItem().getViewBox()
                scene_pos = view_box.mapViewToScene(QtCore.QPointF(float(x_position), float(y_position)))
                plot_pos = self.distribution_plot.mapFromScene(scene_pos)
                if hasattr(plot_pos, "toPoint"):
                    plot_pos = plot_pos.toPoint()
                width = int(editor.width())
                height = int(editor.height())
                left = int(plot_pos.x()) - width if float(anchor_x) >= 0.5 else int(plot_pos.x())
                top = int(plot_pos.y()) - height
            left = max(4, min(left, max(4, self.distribution_plot.width() - width - 4)))
            top = max(4, min(top, max(4, self.distribution_plot.height() - height - 4)))
            editor.move(left, top)
        except Exception:
            pass

    def _begin_distribution_region_endpoint_edit(self, endpoint_index: int) -> None:
        if self.distribution_region is None:
            return
        diameter_region = self._current_distribution_region()
        if diameter_region is None:
            return
        values = sorted(float(value) for value in diameter_region)
        index = 0 if int(endpoint_index) <= 0 else 1
        editor = self._ensure_distribution_region_editor()
        self._editing_distribution_endpoint_index = index
        self._distribution_endpoint_editor_dirty = False
        self._editing_distribution_endpoint = True
        self.distribution_region_label_timer.stop()
        self._set_distribution_region_editor_text(self._format_axis_number(values[index]))
        editor.show()
        editor.raise_()
        self._show_distribution_region_labels(auto_hide=False)
        if index < len(self.distribution_region_labels):
            try:
                editor.setFont(self.distribution_region_labels[index].textItem.font())
            except Exception:
                pass
            self.distribution_region_labels[index].hide()
        editor.setFocus(QtCore.Qt.MouseFocusReason)
        editor.selectAll()

    def _finish_distribution_region_endpoint_edit(self) -> None:
        if self._distribution_endpoint_editor_finishing:
            return
        if not self._distribution_region_editor_visible():
            return
        self._distribution_endpoint_editor_finishing = True
        try:
            self._commit_distribution_region_endpoint_edit()
        finally:
            self._distribution_endpoint_editor_finishing = False

    def _commit_distribution_region_endpoint_edit(self) -> None:
        editor = self.distribution_region_editor
        endpoint_index = self._editing_distribution_endpoint_index
        if editor is None or endpoint_index is None:
            self._hide_distribution_region_editor()
            return
        text = editor.text().strip()
        if not text or not self._distribution_endpoint_editor_dirty:
            self._cancel_distribution_region_endpoint_edit()
            return
        diameter_region = self._current_distribution_region()
        if diameter_region is None:
            self._cancel_distribution_region_endpoint_edit()
            return
        values = sorted(float(value) for value in diameter_region)
        bounds = self._active_diameter_bounds()
        if bounds is None:
            lower_bound, upper_bound = values[0], values[1]
        else:
            lower_bound, upper_bound = sorted(float(value) for value in bounds)
        if not (np.isfinite(lower_bound) and np.isfinite(upper_bound)) or lower_bound >= upper_bound:
            self._cancel_distribution_region_endpoint_edit()
            return

        try:
            new_value = float(text.replace(",", ""))
        except ValueError:
            self.statusBar().showMessage("孔径边界必须是数字。", 4000)
            editor.selectAll()
            QtCore.QTimer.singleShot(0, lambda editor=editor: editor.setFocus(QtCore.Qt.OtherFocusReason))
            return

        span = max(abs(upper_bound - lower_bound), abs(values[1]), 1.0)
        epsilon = span * 1e-9
        if endpoint_index == 0:
            new_value = max(lower_bound, min(float(new_value), values[1] - epsilon))
            if new_value >= values[1]:
                self.statusBar().showMessage("左侧孔径边界必须小于右侧边界。", 4000)
                editor.selectAll()
                QtCore.QTimer.singleShot(0, lambda editor=editor: editor.setFocus(QtCore.Qt.OtherFocusReason))
                return
            values[0] = new_value
        else:
            new_value = min(upper_bound, max(float(new_value), values[0] + epsilon))
            if new_value <= values[0]:
                self.statusBar().showMessage("右侧孔径边界必须大于左侧边界。", 4000)
                editor.selectAll()
                QtCore.QTimer.singleShot(0, lambda editor=editor: editor.setFocus(QtCore.Qt.OtherFocusReason))
                return
            values[1] = new_value

        self._hide_distribution_region_editor()
        try:
            self.distribution_region.setRegion(self._diameter_to_region_values(values[0], values[1]))
        except RuntimeError:
            return
        self._show_distribution_region_labels(auto_hide=True)

    def _cancel_distribution_region_endpoint_edit(self) -> None:
        self._hide_distribution_region_editor()
        self._show_distribution_region_labels(auto_hide=True)

    def _cancel_distribution_region_editor_if_unchanged(self) -> None:
        editor = self.distribution_region_editor
        if editor is None or not self._distribution_region_editor_visible():
            return
        if self._distribution_endpoint_editor_dirty:
            return
        self._hide_distribution_region_editor()

    def _hide_distribution_region_editor(self) -> None:
        editor = self.distribution_region_editor
        if editor is not None:
            try:
                editor.hide()
                editor.clearFocus()
            except RuntimeError:
                pass
        self._editing_distribution_endpoint = False
        self._editing_distribution_endpoint_index = None
        self._distribution_endpoint_editor_dirty = False

    @staticmethod
    def _format_axis_number(value: float) -> str:
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

    def on_distribution_log_changed(self) -> None:
        if self.distribution_region is None:
            return
        raw_region = self._current_distribution_region()
        self.distribution_region_is_log = self._distribution_log_enabled()
        self._syncing_region_changes = True
        try:
            self._update_distribution_region_bounds()
        finally:
            self._syncing_region_changes = False
        if raw_region is not None:
            self._set_distribution_region(raw_region[0], raw_region[1], visible=self.distribution_region.isVisible())
        self.queue_metrics_update()

    def _distribution_log_enabled(self) -> bool:
        controls = self.distribution_plot.getPlotItem().ctrl
        return bool(getattr(controls, "logXCheck").isChecked()) if hasattr(controls, "logXCheck") else False

    def _diameter_to_region_values(self, diameter_min: float, diameter_max: float) -> list[float]:
        lo, hi = sorted((float(diameter_min), float(diameter_max)))
        if self._distribution_log_enabled() and lo > 0 and hi > 0:
            return [float(np.log10(lo)), float(np.log10(hi))]
        return [lo, hi]

    def _region_to_diameter_values(self, region_min: float, region_max: float) -> list[float]:
        lo, hi = sorted((float(region_min), float(region_max)))
        if self.distribution_region_is_log:
            return [float(10.0**lo), float(10.0**hi)]
        return [lo, hi]

    def _current_distribution_region(self) -> list[float] | None:
        if self.distribution_region is None:
            return None
        try:
            return self._region_to_diameter_values(*self.distribution_region.getRegion())
        except RuntimeError:
            return None

    def _set_distribution_region(self, diameter_min: float, diameter_max: float, *, visible: bool = True) -> None:
        if self.distribution_region is None:
            return
        self.distribution_region_is_log = self._distribution_log_enabled()
        self._syncing_region_changes = True
        try:
            self._update_distribution_region_bounds()
            self.distribution_region.setRegion(self._diameter_to_region_values(diameter_min, diameter_max))
            self.distribution_region.setVisible(visible)
        finally:
            self._syncing_region_changes = False
        if visible:
            self._update_distribution_region_labels()
        else:
            self._hide_distribution_region_labels()

    def _update_distribution_region_bounds(self) -> None:
        if self.distribution_region is None:
            return
        bounds = self._active_diameter_bounds()
        if bounds is None:
            self.distribution_region.setBounds((None, None))
            return
        self.distribution_region.setBounds(self._diameter_to_region_values(bounds[0], bounds[1]))

    def _active_diameter_bounds(self) -> list[float] | None:
        if self.result is None:
            return None
        mask = (
            np.isfinite(self.result.diameter)
            & (self.result.diameter > 0)
            & (self.result.is_extrusion < 0.5)
        )
        if not np.any(mask):
            return None
        return [float(np.nanmin(self.result.diameter[mask])), float(np.nanmax(self.result.diameter[mask]))]

    @staticmethod
    def _washburn_constant(result) -> float:
        mask = (
            np.isfinite(result.pressure)
            & (result.pressure > 0)
            & np.isfinite(result.diameter)
            & (result.diameter > 0)
            & (result.is_extrusion < 0.5)
        )
        if not np.any(mask):
            mask = (
                np.isfinite(result.pressure)
                & (result.pressure > 0)
                & np.isfinite(result.diameter)
                & (result.diameter > 0)
            )
        if not np.any(mask):
            return float("nan")
        return float(np.nanmedian(result.pressure[mask] * result.diameter[mask]))

    def _diameter_from_pressure_range(
        self,
        result,
        pressure_min: float,
        pressure_max: float,
    ) -> list[float] | None:
        constant = self._washburn_constant(result)
        if not np.isfinite(constant) or constant <= 0:
            return None
        lo, hi = sorted((float(pressure_min), float(pressure_max)))
        if lo <= 0 or hi <= 0:
            return None
        diameters = [constant / hi, constant / lo]
        return sorted(diameters)

    def _pressure_from_diameter_range(
        self,
        result,
        diameter_min: float,
        diameter_max: float,
    ) -> list[float] | None:
        constant = self._washburn_constant(result)
        if not np.isfinite(constant) or constant <= 0:
            return None
        lo, hi = sorted((float(diameter_min), float(diameter_max)))
        if lo <= 0 or hi <= 0:
            return None
        pressures = [constant / hi, constant / lo]
        return sorted(pressures)

    def queue_metrics_update(self) -> None:
        if self._metrics_pending:
            return
        self._metrics_pending = True
        QtCore.QTimer.singleShot(25, self.update_metrics)

    def update_metrics(self) -> None:
        self._metrics_pending = False
        if not self.results or self.region is None:
            return

        try:
            lo, hi = self._region_to_pressure_values(*self.region.getRegion())
        except RuntimeError:
            return

        self._refresh_all_metric_tables(lo, hi)
        self._update_distribution_selection(lo, hi)

    def _refresh_all_metric_tables(self, pressure_min: float, pressure_max: float) -> None:
        for index, result in enumerate(self.results):
            if index >= len(self.metric_tables):
                continue
            table = self.metric_tables[index]
            metrics = metrics_for_pressure_range(result, pressure_min, pressure_max)
            self._set_selected_pore_volume_cell(index, metrics.pore_volume)
            summary = summary_metrics(result)
            rows = metrics.as_display_rows()

            summary_rows = [
                ("文件", self._display_text(result.metadata.get("file_name"))),
                ("样品名称", self._display_text(result.metadata.get("sample_name"))),
                ("测试人员", self._display_text(result.metadata.get("operator"))),
                ("提交者", self._display_text(result.metadata.get("submitter"))),
                ("测试时间（创建）", self._display_text(result.metadata.get("created"))),
                ("测试仪器", self._display_text(result.metadata.get("instrument_name"))),
                ("测试软件", self._software_display_text(result)),
                (
                    "膨胀计常数",
                    f"{result.metadata.get('penetrometer_constant_uL_per_pF', 0.0):.6g} uL/pF",
                ),
                ("是否显示", "是" if self.visible_results[index] else "否"),
                ("进汞接触角", self._metric_parameter_text(result, "adv_contact_angle_deg", "deg")),
                ("表面张力", self._metric_parameter_text(result, "surface_tension_dynes_cm", "dynes/cm")),
                ("样品质量", f"{result.metadata.get('sample_mass_g', 0.0):.6g} g"),
                (
                    "总入汞体积",
                    f"{summary.total_intrusion_volume:.6g} mL/g @ {summary.total_intrusion_pressure:.6g} psia",
                ),
                (
                    "总孔面积",
                    f"{summary.total_pore_area:.6g} m^2/g @ {summary.total_intrusion_pressure:.6g} psia",
                ),
                (
                    "中值孔径（体积）",
                    f"{summary.median_volume_diameter:.6g} nm @ {summary.median_volume_pressure:.6g} psia, "
                    f"{summary.median_volume:.6g} mL/g",
                ),
                (
                    "中值孔径（面积）",
                    f"{summary.median_area_diameter:.6g} nm @ {summary.median_area_pressure:.6g} psia, "
                    f"{summary.median_area:.6g} m^2/g",
                ),
                ("平均孔径（4V/A）", f"{summary.average_pore_diameter:.6g} nm"),
                (
                    "体积密度",
                    f"{summary.bulk_density:.6g} g/mL @ {summary.bulk_density_pressure:.2f} psia",
                ),
                (
                    "表观骨架密度",
                    f"{summary.apparent_density:.6g} g/mL @ {summary.apparent_density_pressure:.6g} psia",
                ),
                ("孔隙率", f"{summary.porosity:.6g} %"),
                ("最大压力", f"{result.max_pressure:.6g} psia"),
                ("数据点数", str(result.data_point_count)),
            ]
            table.setRowCount(len(summary_rows) + len(rows))

            for row_index, (name, value) in enumerate(summary_rows + rows):
                table.setItem(row_index, 0, QtWidgets.QTableWidgetItem(name))
                table.setItem(row_index, 1, QtWidgets.QTableWidgetItem(value))

    @staticmethod
    def _display_text(value) -> str:
        text = str(value or "").strip()
        return text if text else "未记录"

    @staticmethod
    def _format_parameter_value(value, suffix: str) -> str:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return ""
        if suffix == "deg":
            return f"{number:.6g}°"
        return f"{number:.6g}"

    def _metric_parameter_text(self, result, value_key: str, suffix: str) -> str:
        value = self._format_parameter_value(result.metadata.get(value_key), suffix)
        return f"{value} {suffix}" if suffix != "deg" and value else value

    def _software_display_text(self, result) -> str:
        name = str(result.metadata.get("analysis_software") or "").strip()
        version = str(result.metadata.get("analysis_software_version") or "").strip()
        if name and version:
            return f"{name} {version}"
        if name:
            return name
        return self._display_text(result.metadata.get("software_version"))

    def _update_distribution_selection(self, pressure_min: float, pressure_max: float) -> None:
        if self.result is None or self.distribution_selected_curve is None:
            return
        if self.active_index >= len(self.visible_results) or not self.visible_results[self.active_index]:
            self._clear_distribution_selection_data()
            if self.distribution_region is not None:
                self.distribution_region.setVisible(False)
            self._hide_distribution_region_labels()
            return

        lo, hi = sorted((float(pressure_min), float(pressure_max)))
        diameter_region = self._diameter_from_pressure_range(self.result, lo, hi)
        if diameter_region is None:
            self._clear_distribution_selection_data()
            if self.distribution_region is not None:
                self.distribution_region.setVisible(False)
            self._hide_distribution_region_labels()
            return

        active_bounds = self._active_diameter_bounds()
        if active_bounds is not None:
            diameter_region[0] = max(active_bounds[0], min(diameter_region[0], active_bounds[1]))
            diameter_region[1] = max(active_bounds[0], min(diameter_region[1], active_bounds[1]))

        if self.distribution_region is not None:
            self._set_distribution_region(diameter_region[0], diameter_region[1], visible=True)

        mask = (
            np.isfinite(self.result.pressure)
            & np.isfinite(self.result.diameter)
            & (self.result.diameter > 0)
            & np.isfinite(self.result.log_diff_intrusion)
            & (self.result.is_extrusion < 0.5)
            & (self.result.pressure >= lo)
            & (self.result.pressure <= hi)
        )

        curve_x, curve_y = self._selected_distribution_curve_from_full_curve(diameter_region[0], diameter_region[1])
        self.distribution_selected_curve.setData(curve_x, curve_y)

        if not np.any(mask):
            if self.distribution_selected_points is not None:
                self.distribution_selected_points.setData([], [])
            return

        x_values = self.result.diameter[mask]
        y_values = np.maximum(self.result.log_diff_intrusion[mask], 0.0)
        order = np.argsort(x_values)
        x_values = x_values[order]
        y_values = y_values[order]

        if self.distribution_selected_points is not None:
            self.distribution_selected_points.setData(x_values, y_values)

    def _selected_distribution_curve_from_full_curve(
        self,
        diameter_min: float,
        diameter_max: float,
    ) -> tuple[np.ndarray, np.ndarray]:
        if self.active_index >= len(self.distribution_curve_data):
            return np.array([]), np.array([])
        data = self.distribution_curve_data[self.active_index]
        if not data:
            return np.array([]), np.array([])
        return clip_log_curve_to_range(
            data["curve_x"],
            data["curve_y"],
            float(diameter_min),
            float(diameter_max),
        )

    def _clear_distribution_selection_data(self) -> None:
        if self.distribution_selected_curve is not None:
            self.distribution_selected_curve.setData([], [])
        if self.distribution_selected_points is not None:
            self.distribution_selected_points.setData([], [])

def _resource_path(relative_path: str) -> Path:
    bundle_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
    return bundle_root / relative_path


def _set_windows_app_id() -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
    except Exception:
        pass


def _app_icon() -> QtGui.QIcon:
    icon_path = _resource_path(f"assets/{APP_ICON_FILE}")
    return QtGui.QIcon(str(icon_path)) if icon_path.exists() else QtGui.QIcon()


def run(argv: list[str] | None = None) -> int:
    _set_windows_app_id()
    app = QtWidgets.QApplication(argv or sys.argv)
    app.setApplicationName("MercurySmpAnalyzerZh")
    app.setApplicationDisplayName("")
    app.setFont(QtGui.QFont("Microsoft YaHei UI", 9))
    icon = _app_icon()
    if not icon.isNull():
        app.setWindowIcon(icon)
    window = MainWindow()
    if not icon.isNull():
        window.setWindowIcon(icon)
    window.show()
    exec_func = getattr(app, "exec", app.exec_)
    return exec_func()
