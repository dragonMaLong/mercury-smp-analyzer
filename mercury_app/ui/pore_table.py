"""BET-style orientation controls for the linked pore-structure result table."""
from __future__ import annotations

from dataclasses import replace
import math

from pyqtgraph.Qt import QtCore, QtGui, QtWidgets

from mercury_app.core import summary_metrics


PORE_HEADERS = (
    "样品", "压汞比表面积 (m²/g)", "体积密度 (g/mL)", "骨架密度 (g/mL)",
    "渗透率 (mdarcy)", "阈值压力 (psia)", "特征长度 (nm)",
    "传导形成因子", "曲折因子", "曲折度",
)
_PORE_ATTRIBUTES = (
    ("permeability_md", 4), ("threshold_pressure_psia", 2),
    ("characteristic_length_nm", 2), ("conductivity_formation_factor", 4),
    ("tortuosity_factor", 4), ("tortuosity", 4),
)


def transpose_icon():
    """The same compact table-and-arrows symbol used by BET."""
    pixmap = QtGui.QPixmap(20, 20)
    pixmap.fill(QtCore.Qt.transparent)
    painter = QtGui.QPainter(pixmap)
    painter.setRenderHint(QtGui.QPainter.Antialiasing)
    painter.setPen(QtGui.QPen(QtGui.QColor("#4b5563"), 1.25,
                            QtCore.Qt.SolidLine, QtCore.Qt.RoundCap, QtCore.Qt.RoundJoin))
    painter.drawRect(QtCore.QRectF(2, 2, 10, 10))
    painter.drawLine(QtCore.QPointF(2, 7), QtCore.QPointF(12, 7))
    painter.drawLine(QtCore.QPointF(7, 2), QtCore.QPointF(7, 12))
    for start, end in (((4, 17), (17, 17)), ((14, 14), (17, 17)),
                       ((14, 19), (17, 17)), ((17, 4), (17, 12)),
                       ((14, 9), (17, 12)), ((19, 9), (17, 12))):
        painter.drawLine(QtCore.QPointF(*start), QtCore.QPointF(*end))
    painter.end()
    return QtGui.QIcon(pixmap)


class PoreTableController(QtCore.QObject):
    """Keep sample identities and calculations unchanged when transposing.

    ``window.app_settings`` owns the setting shared with the export dialog.
    The window delegates table refresh and hover-item mapping to this object;
    its existing numeric sorting and cell-copy handlers remain authoritative.
    """

    def __init__(self, window, table, numeric_item_class):
        super().__init__(table)
        self.window = window
        self.table = table
        self.numeric_item_class = numeric_item_class
        self._refreshing = False
        self._parameter_click = None
        self._layout_signature = None
        self._header_hover = -1
        self.buttons = []
        self.header_views = {}
        self.table._transposed = bool(window.app_settings.samples_as_columns)
        self.views = {view.viewport(): view for view in (table, table._frozen_table)}
        for viewport in self.views:
            viewport.installEventFilter(self)
        table.currentCellChanged.connect(self._on_current_cell_changed)
        # The window's sorting handler expects the canonical metric column.
        try:
            table.horizontalHeader().sectionClicked.disconnect(window._sort_pore_structure_summary)
        except (TypeError, RuntimeError):
            pass
        for header in (table.horizontalHeader(), table.frozen_header()):
            self.header_views[header.viewport()] = header
            header.sectionClicked.connect(self._on_header_clicked)
            header.viewport().setMouseTracking(True)
            header.viewport().installEventFilter(self)
            button = QtWidgets.QToolButton(header.viewport())
            button.setAutoRaise(True)
            button.setIcon(transpose_icon())
            button.setIconSize(QtCore.QSize(18, 18))
            button.setCursor(QtCore.Qt.PointingHandCursor)
            button.setAccessibleName("结果表方向")
            button.clicked.connect(self.toggle)
            header.transpose_button = button
            self.buttons.append((header, button))
            header.sectionResized.connect(self.position_buttons)
            header.geometriesChanged.connect(self.position_buttons)
        self.refresh()

    @property
    def samples_as_columns(self):
        return bool(self.table._transposed)

    def toggle(self, _checked=False):
        self.set_samples_as_columns(not self.samples_as_columns)

    def set_samples_as_columns(self, enabled: bool):
        enabled = bool(enabled)
        if enabled == self.samples_as_columns:
            return
        self.window._on_sample_table_row_hovered(-1)
        for hover_filter in getattr(self.window, "detail_sample_hover_filters", []):
            hover_filter.row = -1
        self.table._selection_dragging = False
        self.table._selection_anchor = QtCore.QPersistentModelIndex()
        self.table.clearSelection()
        self._parameter_click = None
        self._header_hover = -1
        self.table._transposed = enabled
        self.window.app_settings = replace(self.window.app_settings, samples_as_columns=enabled)
        self.window.app_settings.save(self.window.settings)
        self.table.horizontalScrollBar().setValue(0)
        self.table.verticalScrollBar().setValue(0)
        self.refresh()

    def position_buttons(self, *_args):
        for header, button in self.buttons:
            try:
                if not header.count():
                    button.hide()
                    continue
                size = max(16, min(22, header.height() - 2))
                button.setGeometry(header.sectionViewportPosition(0) + header.sectionSize(0) - size - 7,
                                   max(1, (header.height() - size) // 2), size, size)
                target = "行" if self.samples_as_columns else "列"
                button.setToolTip(f"结果表方向：切换为样品作为{target}")
                button.show()
                button.raise_()
            except RuntimeError:
                # Qt tears down the two views at different moments. A final
                # geometry event can arrive after the other header is gone.
                continue

    def _on_header_clicked(self, column):
        if self.samples_as_columns:
            if 0 < column <= len(self.window.results):
                self.window._select_sample_from_curve(column - 1)
        else:
            self.window._sort_pore_structure_summary(column)

    def sample_index_for_cell(self, row, column):
        sample = column - 1 if self.samples_as_columns else row
        return sample if 0 <= sample < len(self.window.results) else -1

    def _on_current_cell_changed(self, row, column, _previous_row, _previous_column):
        if self._refreshing:
            return
        sample = self.sample_index_for_cell(row, column)
        if sample >= 0 and sample != self.window.active_index:
            self.window._select_sample_from_curve(sample)

    def sample_items(self, sample):
        """Items that highlight the same sample in either orientation."""
        table = self.table
        if not 0 <= sample < len(self.window.results):
            return []
        if self.samples_as_columns:
            return [table.horizontalHeaderItem(sample + 1)] + [
                table.item(row, sample + 1) for row in range(table.rowCount())
            ]
        return [table.item(sample, column) for column in range(table.columnCount())]

    def eventFilter(self, obj, event):
        kind = event.type()
        header = self.header_views.get(obj)
        if header is not None:
            if kind in (QtCore.QEvent.Resize, QtCore.QEvent.Show):
                self.position_buttons()
            if self.samples_as_columns:
                if kind == QtCore.QEvent.MouseMove:
                    sample = header.logicalIndexAt(event.pos()) - 1
                    if sample != self._header_hover:
                        self._header_hover = sample
                        self.window._on_sample_table_row_hovered(sample)
                elif kind in (QtCore.QEvent.Leave, QtCore.QEvent.Hide):
                    self._header_hover = -1
                    self.window._on_sample_table_row_hovered(-1)
            return False
        if obj not in self.views or not self.samples_as_columns:
            return False
        index = self.views[obj].indexAt(event.pos()) if hasattr(event, "pos") else QtCore.QModelIndex()
        if kind == QtCore.QEvent.MouseButtonPress and event.button() == QtCore.Qt.LeftButton:
            self._parameter_click = ((index.row(), obj.mapToGlobal(event.pos()))
                                     if index.isValid() and index.column() == 0 and not event.modifiers()
                                     else None)
        elif kind == QtCore.QEvent.MouseMove and self._parameter_click is not None:
            if (obj.mapToGlobal(event.pos()) - self._parameter_click[1]).manhattanLength() >= QtWidgets.QApplication.startDragDistance():
                self._parameter_click = None
        elif kind == QtCore.QEvent.MouseButtonRelease and event.button() == QtCore.Qt.LeftButton:
            pending = self._parameter_click
            self._parameter_click = None
            if pending and index.isValid() and index.column() == 0 and index.row() == pending[0]:
                self.table._selection_dragging = False
                self.table.clearSelection()
                self.window._sort_pore_structure_summary(index.row() + 1)
                return True
        elif kind == QtCore.QEvent.MouseButtonDblClick and index.isValid() and index.column() == 0:
            self._parameter_click = None
            return True
        return False

    def refresh(self):
        table = self.table
        window = self.window
        transposed = self.samples_as_columns
        labels = (["参数"] + [window._sample_row_label(result) for result in window.results]
                  if transposed else list(PORE_HEADERS))
        row_count = len(PORE_HEADERS) - 1 if transposed else len(window.results)
        signature = (transposed, tuple(id(result) for result in window.results))
        changed = signature != self._layout_signature
        self._refreshing = True
        previous_blocked = table.blockSignals(True)
        try:
            if changed:
                table.clearSelection()
                table.clearContents()
            table.setColumnCount(len(labels))
            table.setHorizontalHeaderLabels(labels)
            table.setRowCount(row_count)
            for column in range(table.columnCount()):
                table._frozen_table.setColumnHidden(column, column > 0)
                table.setColumnHidden(column, False)
            if transposed:
                for row, title in enumerate(PORE_HEADERS[1:]):
                    item = QtWidgets.QTableWidgetItem(title)
                    item.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable)
                    item.setToolTip("点击按此参数排序，再次点击反向")
                    table.setItem(row, 0, item)
            for sample, result in enumerate(window.results):
                if not transposed:
                    item = QtWidgets.QTableWidgetItem(window._sample_row_label(result))
                    item.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable)
                    table.setItem(sample, 0, item)
                summary = summary_metrics(result)
                values = [(summary.total_pore_area, 4), (summary.bulk_density, 4), (summary.apparent_density, 4)]
                analysis = window.pore_structure_results[sample] if sample < len(window.pore_structure_results) else None
                values += [(getattr(analysis, attribute, float("nan")), digits) for attribute, digits in _PORE_ATTRIBUTES]
                for metric, (value, digits) in enumerate(values):
                    text = f"{value:.{digits}f}" if math.isfinite(value) else "—"
                    item = self.numeric_item_class(text, value)
                    item.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable)
                    item.setTextAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
                    row, column = (metric, sample + 1) if transposed else (sample, metric + 1)
                    table.setItem(row, column, item)
            for row in range(row_count):
                table.setRowHeight(row, 28)
            if changed:
                metrics = QtGui.QFontMetrics(table.horizontalHeader().font())
                for column, label in enumerate(labels):
                    width = max(126, metrics.horizontalAdvance(label) + 28)
                    if column == 0:
                        width = max(160, *(metrics.horizontalAdvance(label) + 28 for label in PORE_HEADERS[1:])) if transposed else 150
                    table.setColumnWidth(column, min(380, width))
            table._update_frozen_geometry()
            self._layout_signature = signature
        finally:
            table.blockSignals(previous_blocked)
            self._refreshing = False
        self.position_buttons()
        window._apply_sample_row_hover(window._hovered_sample_row, True)
