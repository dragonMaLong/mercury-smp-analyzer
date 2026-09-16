import os
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from mercury_app.ui.main_window import MainWindow, QtCore, QtGui, QtWidgets
from pyqtgraph.Qt import QtTest


@pytest.fixture(scope="module")
def application():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield app
    app.processEvents()


@pytest.mark.skipif(
    not all((ROOT.parent / name).exists() for name in ("1-QC3.0.SMP", "2-TK500.SMP")),
    reason="Local SMP reference files are unavailable",
)
def test_detail_sample_row_hover_is_bidirectional_and_survives_redraw(application):
    app = application
    window = MainWindow()
    window.setAttribute(QtCore.Qt.WA_DontShowOnScreen)
    try:
        window.load_files([ROOT.parent / "1-QC3.0.SMP", ROOT.parent / "2-TK500.SMP"])
        window.show()
        QtTest.QTest.qWait(30)
        names = lambda row: [window.sample_list.item(row, 1), window.selected_pore_volume_table.item(row, 0), window.pore_structure_summary_table.item(row, 0)]

        def row_items(row):
            return [window.sample_list.item(row, 1)] + [
                table.item(row, column)
                for table in (window.selected_pore_volume_table, window.pore_structure_summary_table)
                for column in range(table.columnCount())
            ]

        def hover(view, row, column):
            pos = view.visualRect(view.model().index(row, column)).center()
            app.sendEvent(view.viewport(), QtGui.QMouseEvent(
                QtCore.QEvent.MouseMove, QtCore.QPointF(pos), QtCore.Qt.NoButton,
                QtCore.Qt.NoButton, QtCore.Qt.NoModifier,
            ))

        for tab in (1, 2):
            window.detail_tabs.setCurrentIndex(tab)
            app.processEvents()
            table = window.selected_pore_volume_table if tab == 1 else window.pore_structure_summary_table
            if tab == 2:
                table.horizontalScrollBar().setValue(table.horizontalScrollBar().maximum())
                app.processEvents()
            view = table if tab == 1 else table._frozen_table
            hover(view, 1, 0)
            assert all(item.font().bold() for item in row_items(1))
            assert not any(item.font().bold() for item in row_items(0))
            assert window.active_index == 0
            assert not window.pore_structure_summary_table.selectedIndexes()
            assert window.pressure_plot._sample_curve_interaction_controller.hovered_sample_index == 1
            window.update_pore_structure()
            assert all(item.font().bold() for item in row_items(1))
            assert window.pore_structure_pressure_plot._sample_curve_interaction_controller.hovered_sample_index == 1
            app.sendEvent(view.viewport(), QtCore.QEvent(QtCore.QEvent.Leave))
            assert not any(item.font().bold() for item in row_items(1))
            assert window.pressure_plot._sample_curve_interaction_controller.hovered_sample_index is None

            # Numeric cells link the whole row too, including while scrolled.
            hover(table, 1, table.columnCount() - 1)
            assert all(item.font().bold() for item in row_items(1))
            assert window.pressure_plot._sample_curve_interaction_controller.hovered_sample_index == 1
            hover(table, 0, table.columnCount() - 1)
            assert all(item.font().bold() for item in row_items(0))
            assert not any(item.font().bold() for item in row_items(1))
            assert window.active_index == 0
            assert not window.pore_structure_summary_table.selectedIndexes()
            app.sendEvent(table.viewport(), QtCore.QEvent(QtCore.QEvent.Leave))
            assert not any(item.font().bold() for item in row_items(0))

        # Exercise the real curve controller's notification path back to tables.
        controller = window.pressure_plot._sample_curve_interaction_controller
        entry = next(entry for entry in controller.entries if entry["sample_index"] == 1)
        controller._set_hover(entry, None)
        assert all(item.font().bold() for item in row_items(1))
        controller.clear_hover()
        assert not any(item.font().bold() for item in row_items(1))

        # Hover mapping still follows the reordered rows after sorting.
        window.sort_samples_by_test_time(False)
        window.detail_tabs.setCurrentIndex(1)
        app.processEvents()
        hover(window.selected_pore_volume_table, 0, 0)
        assert all(item.font().bold() for item in row_items(0))
        assert len({item.text() for item in names(0)}) == 1
        assert not any(item.font().bold() for item in row_items(1))
    finally:
        window.close()
        app.processEvents()
