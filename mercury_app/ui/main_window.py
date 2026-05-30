from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np

os.environ.setdefault("PYQTGRAPH_QT_LIB", "PyQt5")

import pyqtgraph as pg
from pyqtgraph.Qt import QT_LIB, QtCore, QtGui, QtWidgets

from mercury_app.core import calculate_microactive, export_results_xlsx, load_smp, metrics_for_pressure_range, summary_metrics
from mercury_app.ui.plots import (
    DEFAULT_COLORS,
    make_plot,
    plot_distribution_multi,
    plot_pressure_volume_multi,
    smooth_log_distribution_curve,
)


class SelectAllCheckBox(QtWidgets.QCheckBox):
    def nextCheckState(self) -> None:
        if self.checkState() == QtCore.Qt.Checked:
            self.setCheckState(QtCore.Qt.Unchecked)
        else:
            self.setCheckState(QtCore.Qt.Checked)


def _check_state_value(state) -> int:
    value = getattr(state, "value", state)
    return int(value)


VISIBLE_COLUMN = 0
FILE_COLUMN = 1
TEST_TIME_COLUMN = 2
ANGLE_COLUMN = 3
TENSION_COLUMN = 4


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.result = None
        self.results = []
        self.visible_results = []
        self.active_index = 0
        self.metric_tables = []
        self.sample_items = []
        self.sample_colors = list(DEFAULT_COLORS)
        self._updating_sample_checks = False
        self.test_time_sort_ascending = False
        self.region = None
        self.pressure_region_is_log = False
        self.distribution_region = None
        self.distribution_selected_curve = None
        self.distribution_selected_points = None
        self._metrics_pending = False

        self.setWindowTitle(f"Mercury Intrusion MVP ({QT_LIB})")
        self.resize(1200, 760)

        open_button = QtWidgets.QPushButton("Open SMP")
        open_button.clicked.connect(self.open_file)
        open_button.setMinimumHeight(34)
        add_button = QtWidgets.QPushButton("Add SMP")
        add_button.clicked.connect(self.add_files)
        add_button.setMinimumHeight(30)
        export_button = QtWidgets.QPushButton("Export XLS")
        export_button.clicked.connect(self.export_xls)
        export_button.setMinimumHeight(30)

        self.pressure_plot = make_plot(
            "Pressure vs Cumulative Pore Volume",
            "Cumulative Pore Volume (mL/g)",
            "Pressure (psia)",
        )
        self._connect_pressure_log_controls()
        self.distribution_plot = make_plot(
            "Pore Size Distribution",
            "dV/dlogD (mL/g)",
            "Pore Diameter (nm)",
        )
        self._connect_distribution_log_controls()

        plot_stack = QtWidgets.QWidget()
        plot_layout = QtWidgets.QVBoxLayout(plot_stack)
        plot_layout.setContentsMargins(0, 0, 0, 0)
        plot_layout.addWidget(self.distribution_plot, 3)
        plot_layout.addWidget(self.pressure_plot, 2)

        side_panel = QtWidgets.QWidget()
        side_layout = QtWidgets.QVBoxLayout(side_panel)
        side_layout.setContentsMargins(6, 6, 6, 6)
        side_layout.setSpacing(6)
        side_layout.addWidget(open_button)
        side_layout.addWidget(add_button)
        side_layout.addWidget(export_button)

        self.select_all_check = SelectAllCheckBox()
        self.select_all_check.setTristate(True)
        self.select_all_check.setCheckState(QtCore.Qt.Checked)
        self.select_all_check.setCursor(QtCore.Qt.PointingHandCursor)
        self.select_all_check.setToolTip("Show or hide all samples")
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

        self.sample_list = QtWidgets.QTableWidget(0, 5)
        self.sample_list.setHorizontalHeaderLabels(["", "File name", "Test time", "Angle", "Surface"])
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
        self.sample_list.horizontalHeaderItem(TEST_TIME_COLUMN).setToolTip("Click to sort by test time")
        self.sample_list.horizontalHeaderItem(ANGLE_COLUMN).setTextAlignment(QtCore.Qt.AlignCenter)
        self.sample_list.horizontalHeaderItem(TENSION_COLUMN).setTextAlignment(QtCore.Qt.AlignCenter)
        self.sample_list.verticalHeader().setVisible(False)
        self.sample_list.verticalHeader().setDefaultSectionSize(28)
        self.sample_list.setShowGrid(False)
        self.sample_list.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.sample_list.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.sample_list.setEditTriggers(
            QtWidgets.QAbstractItemView.DoubleClicked
            | QtWidgets.QAbstractItemView.EditKeyPressed
            | QtWidgets.QAbstractItemView.AnyKeyPressed
        )
        self.sample_list.setVerticalScrollMode(QtWidgets.QAbstractItemView.ScrollPerPixel)
        self.sample_list.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        self.sample_list.setMinimumHeight(60)
        self.sample_list.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.sample_list.setColumnWidth(VISIBLE_COLUMN, 30)
        self.sample_list.setColumnWidth(FILE_COLUMN, 180)
        self.sample_list.setColumnWidth(TEST_TIME_COLUMN, 190)
        self.sample_list.setColumnWidth(ANGLE_COLUMN, 104)
        self.sample_list.setColumnWidth(TENSION_COLUMN, 118)
        self.sample_list.currentCellChanged.connect(self.on_active_cell_changed)
        self.sample_list.itemChanged.connect(self.on_sample_item_changed)
        self.sample_list.itemClicked.connect(self.on_sample_item_clicked)
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
        self.select_all_check.setParent(sample_header)
        self.select_all_check.show()
        self.angle_info_button = self._make_header_info_button(
            "Advancing contact angle",
            (
                "Advancing contact angle is the mercury contact angle used while mercury is intruding into pores.<br><br>"
                "It represents how non-wetting mercury is against the sample surface. In the Washburn equation, it directly affects the pressure-to-pore-diameter conversion, so changing it will shift the pore diameter and pore size distribution.<br><br>"
                "In practice, this value is usually obtained from a contact-angle measurement on a representative surface, or selected from the lab's validated method for similar materials.<br><br>"
                "The current value is the value stored in the SMP/MicroActive method. When the file has no valid value, this software falls back to the common Micromeritics recommendation: 130 degrees."
            ),
        )
        self.surface_info_button = self._make_header_info_button(
            "Surface tension",
            (
                "Surface tension is the mercury surface tension used in the Washburn equation.<br><br>"
                "It describes the energy at the mercury surface. Together with contact angle and pressure, it determines the calculated pore diameter, so changing it will also shift the pore size distribution.<br><br>"
                "In practice, this value comes from mercury property data under the test conditions, or from the lab/instrument method standard used for AutoPore analysis.<br><br>"
                "The current value is the value stored in the SMP/MicroActive method. When the file has no valid value, this software falls back to the common Micromeritics recommendation: 485 dynes/cm."
            ),
        )

        sample_panel = QtWidgets.QWidget()
        sample_panel_layout = QtWidgets.QVBoxLayout(sample_panel)
        sample_panel_layout.setContentsMargins(0, 0, 0, 0)
        sample_panel_layout.setSpacing(0)
        sample_panel_layout.addWidget(self.sample_list, 1)
        sample_panel.setMinimumHeight(88)

        self.metrics_stack = QtWidgets.QStackedWidget()

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

        splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        splitter.addWidget(side_panel)
        splitter.addWidget(plot_stack)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)
        splitter.setSizes([300, 900])

        self.setCentralWidget(splitter)
        self._sync_select_all_state()
        QtCore.QTimer.singleShot(0, self._position_header_controls)

    def _connect_pressure_log_controls(self) -> None:
        controls = self.pressure_plot.getPlotItem().ctrl
        if hasattr(controls, "logXCheck"):
            controls.logXCheck.stateChanged.connect(self.on_pressure_log_changed)

    def _connect_distribution_log_controls(self) -> None:
        controls = self.distribution_plot.getPlotItem().ctrl
        if hasattr(controls, "logXCheck"):
            controls.logXCheck.stateChanged.connect(self.queue_metrics_update)

    def open_file(self) -> None:
        file_paths, _ = QtWidgets.QFileDialog.getOpenFileNames(
            self,
            "Open SMP",
            str(Path.cwd()),
            "SMP files (*.SMP *.smp)",
        )
        if not file_paths:
            return

        try:
            self.load_files(file_paths)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Load failed", str(exc))

    def add_files(self) -> None:
        file_paths, _ = QtWidgets.QFileDialog.getOpenFileNames(
            self,
            "Add SMP",
            str(Path.cwd()),
            "SMP files (*.SMP *.smp)",
        )
        if not file_paths:
            return

        try:
            self.append_files(file_paths)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Add failed", str(exc))

    def export_xls(self) -> None:
        selected_results = [
            result
            for result, visible in zip(self.results, self.visible_results)
            if visible
        ]
        if not selected_results:
            QtWidgets.QMessageBox.information(self, "Export XLS", "No selected SMP files to export.")
            return

        default_name = f"mercury_export_{len(selected_results)}_samples.xlsx"
        file_path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "Export XLS",
            str(Path.cwd() / default_name),
            "Excel workbook (*.xlsx)",
        )
        if not file_path:
            return

        try:
            output_path = export_results_xlsx(selected_results, file_path)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Export failed", str(exc))
            return

        QtWidgets.QMessageBox.information(
            self,
            "Export complete",
            f"Exported {len(selected_results)} sample(s) to:\n{output_path}",
        )

    def load_file(self, file_path: str | Path) -> None:
        self.load_files([file_path])

    def load_files(self, file_paths) -> None:
        self._remove_region()
        self.results = [load_smp(file_path) for file_path in file_paths]
        self.visible_results = [True] * len(self.results)
        self.active_index = 0
        self.result = self.results[0] if self.results else None
        self.setWindowTitle(f"Mercury Intrusion MVP ({QT_LIB}) - {len(self.results)} sample(s)")

        self._build_metric_tabs()
        self._redraw_plots()
        self._add_distribution_selection_items()

        pressure = self._all_pressure_values()
        if pressure.size == 0:
            return

        lo = float(np.nanmin(pressure))
        hi = float(np.nanmax(pressure))
        span = hi - lo
        region_lo = lo + span * 0.25
        region_hi = lo + span * 0.55

        self.pressure_region_is_log = self._pressure_log_enabled()
        self.region = pg.LinearRegionItem(
            self._pressure_to_region_values(region_lo, region_hi),
            bounds=self._pressure_to_region_values(lo, hi),
            movable=True,
        )
        self.region.sigRegionChanged.connect(self.queue_metrics_update)
        self.pressure_plot.addItem(self.region, ignoreBounds=True)
        self.update_metrics()

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

        self.setWindowTitle(f"Mercury Intrusion MVP ({QT_LIB}) - {len(self.results)} sample(s)")
        self._build_metric_tabs()
        self._redraw_plots()
        self._add_distribution_selection_items()

        pressure = self._all_pressure_values()
        if pressure.size == 0:
            return

        if raw_region is None:
            lo = float(np.nanmin(pressure))
            hi = float(np.nanmax(pressure))
            span = hi - lo
            raw_region = [lo + span * 0.25, lo + span * 0.55]

        self.pressure_region_is_log = self._pressure_log_enabled()
        self.region = pg.LinearRegionItem(
            self._pressure_to_region_values(raw_region[0], raw_region[1]),
            bounds=self._pressure_to_region_values(np.nanmin(pressure), np.nanmax(pressure)),
            movable=True,
        )
        self.region.sigRegionChanged.connect(self.queue_metrics_update)
        self.pressure_plot.addItem(self.region, ignoreBounds=True)
        self.update_metrics()

    def _redraw_plots(self) -> None:
        plot_distribution_multi(self.distribution_plot, self.results, self.visible_results, self.sample_colors)
        plot_pressure_volume_multi(self.pressure_plot, self.results, self.visible_results, self.sample_colors)

    def _build_metric_tabs(self, active_index: int | None = None) -> None:
        self._build_metric_tabs_with_options(active_index=active_index, preserve_column_widths=False)

    def _build_metric_tabs_with_options(
        self,
        active_index: int | None = None,
        preserve_column_widths: bool = False,
    ) -> None:
        column_widths = self._sample_column_widths() if preserve_column_widths else None
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
        self.sample_list.blockSignals(False)
        if column_widths:
            self._restore_sample_column_widths(column_widths)
        else:
            self._resize_sample_columns_to_contents()
        self._sync_select_all_state()

        if self.results:
            index = 0 if active_index is None else max(0, min(active_index, len(self.results) - 1))
            self.on_active_tab_changed(index)

    def _sample_column_widths(self) -> list[int]:
        return [self.sample_list.columnWidth(column) for column in range(self.sample_list.columnCount())]

    def _restore_sample_column_widths(self, widths: list[int]) -> None:
        for column, width in enumerate(widths[: self.sample_list.columnCount()]):
            self.sample_list.setColumnWidth(column, width)
        self._position_header_controls()

    def _resize_sample_columns_to_contents(self) -> None:
        self.sample_list.setColumnWidth(VISIBLE_COLUMN, 30)
        self.sample_list.resizeColumnToContents(FILE_COLUMN)
        self.sample_list.setColumnWidth(FILE_COLUMN, max(180, self.sample_list.columnWidth(FILE_COLUMN) + 18))
        self.sample_list.setColumnWidth(TEST_TIME_COLUMN, 190)
        self.sample_list.resizeColumnToContents(ANGLE_COLUMN)
        self.sample_list.setColumnWidth(ANGLE_COLUMN, max(104, self.sample_list.columnWidth(ANGLE_COLUMN) + 24))
        self.sample_list.resizeColumnToContents(TENSION_COLUMN)
        self.sample_list.setColumnWidth(TENSION_COLUMN, max(118, self.sample_list.columnWidth(TENSION_COLUMN) + 24))
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
        header = self.sample_list.horizontalHeader()
        if not header.isVisible():
            return
        size = self.select_all_check.sizeHint()
        x = header.sectionViewportPosition(VISIBLE_COLUMN) + (header.sectionSize(VISIBLE_COLUMN) - size.width()) // 2
        y = (header.height() - size.height()) // 2
        self.select_all_check.setGeometry(max(0, x), max(0, y), size.width(), size.height())
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
        button.setGeometry(max(0, x), max(0, y), size.width(), size.height())

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

        self.sample_list.setCurrentCell(index, FILE_COLUMN)

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
        delete_action = menu.addAction("Delete")
        global_position = self.sample_list.viewport().mapToGlobal(position)
        selected_action = menu.exec_(global_position) if hasattr(menu, "exec_") else menu.exec(global_position)
        if selected_action == delete_action:
            self.delete_sample(index)

    def delete_sample(self, index: int) -> None:
        if not (0 <= index < len(self.results)):
            return

        raw_region = self._current_pressure_region()
        self._remove_region()
        del self.results[index]
        del self.visible_results[index]

        target_index = min(index, len(self.results) - 1) if self.results else 0
        self.active_index = target_index
        self.result = self.results[target_index] if self.results else None
        self.setWindowTitle(f"Mercury Intrusion MVP ({QT_LIB}) - {len(self.results)} sample(s)")

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
            raw_region = self._default_pressure_region(pressure)
        else:
            raw_region = self._clamp_pressure_region(raw_region, pressure)

        self.pressure_region_is_log = self._pressure_log_enabled()
        self.region = pg.LinearRegionItem(
            self._pressure_to_region_values(raw_region[0], raw_region[1]),
            bounds=self._pressure_to_region_values(np.nanmin(pressure), np.nanmax(pressure)),
            movable=True,
        )
        self.region.sigRegionChanged.connect(self.queue_metrics_update)
        self.pressure_plot.addItem(self.region, ignoreBounds=True)
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
        test_time_item.setToolTip("Test time from SMP created timestamp")
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

    def _make_metric_table(self) -> QtWidgets.QTableWidget:
        table = QtWidgets.QTableWidget(0, 2)
        table.setHorizontalHeaderLabels(["Metric", "Value"])
        table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.Stretch)
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        return table

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

    def on_sample_header_clicked(self, section: int) -> None:
        if section != TEST_TIME_COLUMN or len(self.results) < 2:
            return
        self.test_time_sort_ascending = not self.test_time_sort_ascending
        self.sort_samples_by_test_time(self.test_time_sort_ascending)

    def sort_samples_by_test_time(self, ascending: bool) -> None:
        active_result = self.result
        pairs = list(zip(self.results, self.visible_results))
        pairs.sort(key=lambda pair: self._test_time_sort_key(pair[0]), reverse=not ascending)
        self.results = [pair[0] for pair in pairs]
        self.visible_results = [pair[1] for pair in pairs]

        active_index = 0
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

    def on_active_cell_changed(self, current_row: int, current_column: int, previous_row: int, previous_column: int) -> None:
        self.on_active_tab_changed(current_row)

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
            self.statusBar().showMessage("Cannot recalculate: raw SMP data is unavailable.", 5000)
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
            self.statusBar().showMessage(f"Recalculate failed: {exc}", 5000)
            return

        self.results[index] = updated
        if index == self.active_index:
            self.result = updated
        self._restore_parameter_cells(index)
        self._refresh_visibility_dependent_ui()

    def _parameter_value_from_cell(self, row: int, column: int) -> float:
        item = self.sample_list.item(row, column)
        if item is None:
            raise ValueError("Calculation parameter is missing.")
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
            raise ValueError("Calculation parameter cannot be empty.")
        try:
            return float(normalized)
        except ValueError as exc:
            raise ValueError("Calculation parameter must be a number.") from exc

    @staticmethod
    def _validate_calculation_parameters(theta: float, gamma: float) -> None:
        if not 90.0 < theta < 180.0:
            raise ValueError("Advancing contact angle must be between 90 and 180 degrees.")
        if not 100.0 < gamma < 600.0:
            raise ValueError("Surface tension must be between 100 and 600 dynes/cm.")

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
                self.pressure_region_is_log = self._pressure_log_enabled()
                self.region = pg.LinearRegionItem(
                    self._pressure_to_region_values(raw_region[0], raw_region[1]),
                    bounds=self._pressure_to_region_values(np.nanmin(pressure), np.nanmax(pressure)),
                    movable=True,
                )
                self.region.sigRegionChanged.connect(self.queue_metrics_update)
                self.pressure_plot.addItem(self.region, ignoreBounds=True)
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

    def _clamp_pressure_region(self, raw_region: list[float], pressure: np.ndarray) -> list[float]:
        lo = float(np.nanmin(pressure))
        hi = float(np.nanmax(pressure))
        region_lo, region_hi = sorted((float(raw_region[0]), float(raw_region[1])))
        region_lo = max(lo, min(region_lo, hi))
        region_hi = max(lo, min(region_hi, hi))
        if region_lo < region_hi:
            return [region_lo, region_hi]
        return self._default_pressure_region(pressure)

    def _remove_region(self) -> None:
        if self.region is None:
            self._remove_distribution_selection_items()
            return
        try:
            self.region.sigRegionChanged.disconnect(self.queue_metrics_update)
        except (RuntimeError, TypeError):
            pass
        try:
            self.pressure_plot.removeItem(self.region)
        except RuntimeError:
            pass
        self.region = None
        self.pressure_region_is_log = False
        self._remove_distribution_selection_items()

    def on_pressure_log_changed(self) -> None:
        if not self.results or self.region is None:
            return
        raw_lo, raw_hi = self._region_to_pressure_values(*self.region.getRegion())
        self.pressure_region_is_log = self._pressure_log_enabled()
        pressure = self._all_pressure_values()
        if pressure.size:
            self.region.setBounds(self._pressure_to_region_values(np.nanmin(pressure), np.nanmax(pressure)))
        self.region.setRegion(self._pressure_to_region_values(raw_lo, raw_hi))
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
        self.distribution_region = pg.LinearRegionItem([0.0, 0.0], movable=False)
        self.distribution_region.setVisible(False)
        self.distribution_plot.addItem(self.distribution_region, ignoreBounds=True)
        self.distribution_selected_curve = self.distribution_plot.plot(
            [],
            [],
            pen=pg.mkPen("#dc2626", width=3),
        )
        self.distribution_selected_points = self.distribution_plot.plot(
            [],
            [],
            pen=None,
            symbol="o",
            symbolSize=7,
            symbolPen=pg.mkPen("#dc2626", width=1),
            symbolBrush=pg.mkBrush("#fee2e2"),
        )

    def _remove_distribution_selection_items(self) -> None:
        if self.distribution_region is not None:
            try:
                self.distribution_plot.removeItem(self.distribution_region)
            except RuntimeError:
                pass
            self.distribution_region = None
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
            summary = summary_metrics(result)
            rows = metrics.as_display_rows()

            summary_rows = [
                ("File", self._display_text(result.metadata.get("file_name"))),
                ("Sample name", self._display_text(result.metadata.get("sample_name"))),
                ("Operator", self._display_text(result.metadata.get("operator"))),
                ("Submitter", self._display_text(result.metadata.get("submitter"))),
                ("Test time (created)", self._display_text(result.metadata.get("created"))),
                ("Instrument", self._display_text(result.metadata.get("instrument_name"))),
                ("Software", self._software_display_text(result)),
                (
                    "Penetrometer constant",
                    f"{result.metadata.get('penetrometer_constant_uL_per_pF', 0.0):.6g} uL/pF",
                ),
                ("Visible", "Yes" if self.visible_results[index] else "No"),
                ("Advancing contact angle", self._metric_parameter_text(result, "adv_contact_angle_deg", "deg")),
                ("Surface tension", self._metric_parameter_text(result, "surface_tension_dynes_cm", "dynes/cm")),
                ("Mass", f"{result.metadata.get('sample_mass_g', 0.0):.6g} g"),
                (
                    "Total intrusion volume",
                    f"{summary.total_intrusion_volume:.6g} mL/g @ {summary.total_intrusion_pressure:.6g} psia",
                ),
                (
                    "Total pore area",
                    f"{summary.total_pore_area:.6g} m^2/g @ {summary.total_intrusion_pressure:.6g} psia",
                ),
                (
                    "Median pore diameter (volume)",
                    f"{summary.median_volume_diameter:.6g} nm @ {summary.median_volume_pressure:.6g} psia, "
                    f"{summary.median_volume:.6g} mL/g",
                ),
                (
                    "Median pore diameter (area)",
                    f"{summary.median_area_diameter:.6g} nm @ {summary.median_area_pressure:.6g} psia, "
                    f"{summary.median_area:.6g} m^2/g",
                ),
                ("Average pore diameter (4V/A)", f"{summary.average_pore_diameter:.6g} nm"),
                (
                    "Bulk density",
                    f"{summary.bulk_density:.6g} g/mL @ {summary.bulk_density_pressure:.2f} psia",
                ),
                (
                    "Apparent skeletal density",
                    f"{summary.apparent_density:.6g} g/mL @ {summary.apparent_density_pressure:.6g} psia",
                ),
                ("Porosity", f"{summary.porosity:.6g} %"),
                ("Max pressure", f"{result.max_pressure:.6g} psia"),
                ("Data points", str(result.data_point_count)),
            ]
            table.setRowCount(len(summary_rows) + len(rows))

            for row_index, (name, value) in enumerate(summary_rows + rows):
                table.setItem(row_index, 0, QtWidgets.QTableWidgetItem(name))
                table.setItem(row_index, 1, QtWidgets.QTableWidgetItem(value))

    @staticmethod
    def _display_text(value) -> str:
        text = str(value or "").strip()
        return text if text else "NA"

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
            return

        lo, hi = sorted((float(pressure_min), float(pressure_max)))
        mask = (
            np.isfinite(self.result.pressure)
            & np.isfinite(self.result.diameter)
            & (self.result.diameter > 0)
            & np.isfinite(self.result.log_diff_intrusion)
            & (self.result.is_extrusion < 0.5)
            & (self.result.pressure >= lo)
            & (self.result.pressure <= hi)
        )
        if not np.any(mask):
            self._clear_distribution_selection_data()
            if self.distribution_region is not None:
                self.distribution_region.setVisible(False)
            return

        x_values = self.result.diameter[mask]
        y_values = np.maximum(self.result.log_diff_intrusion[mask], 0.0)
        order = np.argsort(x_values)
        x_values = x_values[order]
        y_values = y_values[order]

        if self.distribution_region is not None:
            self.distribution_region.setRegion(self._distribution_region_values(x_values))
            self.distribution_region.setVisible(True)
        curve_x, curve_y = smooth_log_distribution_curve(x_values, y_values)
        self.distribution_selected_curve.setData(curve_x, curve_y)
        if self.distribution_selected_points is not None:
            self.distribution_selected_points.setData(x_values, y_values)

    def _clear_distribution_selection_data(self) -> None:
        if self.distribution_selected_curve is not None:
            self.distribution_selected_curve.setData([], [])
        if self.distribution_selected_points is not None:
            self.distribution_selected_points.setData([], [])

    def _distribution_region_values(self, diameters: np.ndarray) -> list[float]:
        x_min = float(np.nanmin(diameters))
        x_max = float(np.nanmax(diameters))
        controls = self.distribution_plot.getPlotItem().ctrl
        log_x_enabled = bool(getattr(controls, "logXCheck").isChecked()) if hasattr(controls, "logXCheck") else False
        if log_x_enabled and x_min > 0 and x_max > 0:
            return [float(np.log10(x_min)), float(np.log10(x_max))]
        return [x_min, x_max]


def run(argv: list[str] | None = None) -> int:
    app = QtWidgets.QApplication(argv or sys.argv)
    app.setFont(QtGui.QFont("Microsoft YaHei UI", 9))
    window = MainWindow()
    window.show()
    exec_func = getattr(app, "exec", app.exec_)
    return exec_func()
