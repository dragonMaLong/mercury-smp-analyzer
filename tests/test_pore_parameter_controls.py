import os
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from mercury_app.core import summary_metrics
from mercury_app.ui.main_window import MainWindow, QtCore, QtGui, QtWidgets
from pyqtgraph.Qt import QtTest


@pytest.fixture(scope="module")
def application():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield app
    app.processEvents()


def test_pore_parameter_units_are_outside_inputs(application):
    window = MainWindow()
    window.setAttribute(QtCore.Qt.WA_DontShowOnScreen)
    try:
        window.analysis_tabs.setCurrentIndex(2)
        window.show()
        application.processEvents()
        assert window.pore_calculated_surface_area_edit.text() == "—"
        assert window.pore_surface_area_mode_combo.currentIndex() == 1
        assert not window.pore_surface_area_spin.isEnabled()
        for control, unit in (
            (window.pore_surface_area_spin, "m²/g"),
            (window.pore_calculated_surface_area_edit, "m²/g"),
            (window.pore_bulk_density_spin, "g/mL"),
            (window.pore_skeletal_density_spin, "g/mL"),
        ):
            row = control.parentWidget().layout()
            label = row.itemAt(1).widget()
            assert row.itemAt(0).widget() is control
            assert label.text() == unit
            assert label.x() > control.geometry().right()
            assert label.width() >= label.fontMetrics().horizontalAdvance(unit)
            assert control.width() > 50
            if isinstance(control, QtWidgets.QDoubleSpinBox):
                assert control.suffix() == ""
        assert window.pore_calculated_surface_area_edit.isReadOnly()
        assert window.pore_calculated_surface_area_edit.toolTip() == "当前样品由压汞数据计算的比表面积。"
        for entered_density in (False, True, False):
            window.pore_use_entered_density_check.setChecked(entered_density)
            window._update_pore_structure_control_states()
            area_palette = window.pore_calculated_surface_area_edit.palette()
            density_palette = window.pore_bulk_density_spin.lineEdit().palette()
            for group in (QtGui.QPalette.Active, QtGui.QPalette.Inactive):
                for role in (QtGui.QPalette.Base, QtGui.QPalette.Text):
                    assert area_palette.color(group, role) == density_palette.color(QtGui.QPalette.Disabled, role)
            assert window.pore_calculated_surface_area_edit.isEnabled()
    finally:
        window.close()
        application.processEvents()


@pytest.mark.skipif(
    not all((ROOT.parent / name).exists() for name in ("1-QC3.0.SMP", "2-TK500.SMP")),
    reason="Local SMP reference files are unavailable",
)
def test_summary_calculated_columns_ignore_overrides_and_sort_all_samples(application):
    window = MainWindow()
    window.setAttribute(QtCore.Qt.WA_DontShowOnScreen)
    try:
        window.load_files([ROOT.parent / "1-QC3.0.SMP", ROOT.parent / "2-TK500.SMP"])
        table = window.pore_structure_summary_table

        def calculated_rows():
            return [[table.item(row, col).text() for col in (1, 2, 3)] for row in range(2)]

        expected = []
        for result in window.results:
            summary = summary_metrics(result)
            expected.append([f"{value:.4f}" for value in (
                summary.total_pore_area, summary.bulk_density, summary.apparent_density,
            )])
        assert calculated_rows() == expected
        for column in (1, 2, 3):
            assert table.horizontalHeaderItem(column).toolTip() == ""
            for row in range(table.rowCount()):
                assert table.item(row, column).toolTip() == ""
        window.pore_surface_area_mode_combo.setCurrentIndex(0)
        window.pore_surface_area_spin.setValue(777.)
        window.pore_use_entered_density_check.setChecked(True)
        window.pore_bulk_density_spin.setValue(.2)
        window.pore_skeletal_density_spin.setValue(1.2)
        assert window.pore_structure_results[0].surface_area_m2g == 777.
        assert window.pore_structure_results[0].bulk_density_gmL == .2
        assert window.pore_structure_results[0].skeletal_density_gmL == 1.2
        assert calculated_rows() == expected

        for column, attribute in ((1, "total_pore_area"), (2, "bulk_density"), (3, "apparent_density")):
            for ascending in (True, False):
                expected_results = sorted(window.results, key=lambda r: getattr(summary_metrics(r), attribute), reverse=not ascending)
                window._sort_pore_structure_summary(column)
                # Process each interaction as the real GUI event loop would,
                # before rebuilding the plots again on the next header click.
                application.processEvents()
                assert [id(r) for r in window.results] == [id(r) for r in expected_results]
                for row, result in enumerate(window.results):
                    assert table.item(row, 0).text() == window.sample_list.item(row, 1).text()
                    assert table.item(row, column).text() == f"{getattr(summary_metrics(result), attribute):.4f}"
                assert not table.selectedIndexes()

        table.setRangeSelected(QtWidgets.QTableWidgetSelectionRange(0, 1, 1, 3), True)
        window._copy_pore_structure_selection()
        lines = application.clipboard().text().splitlines()
        assert lines[0].split('\t') == [table.horizontalHeaderItem(c).text() for c in range(4)]
        assert [line.split('\t') for line in lines[1:]] == [
            [table.item(row, col).text() for col in range(4)] for row in range(2)
        ]
    finally:
        application.clipboard().clear()
        window.close()
        application.processEvents()


@pytest.mark.skipif(
    not all((ROOT.parent / name).exists() for name in ("1-QC3.0.SMP", "2-TK500.SMP")),
    reason="Local SMP reference files are unavailable",
)
def test_calculated_surface_area_tracks_sample_and_preserves_bet(application):
    window = MainWindow()
    window.setAttribute(QtCore.Qt.WA_DontShowOnScreen)
    try:
        window.load_files([ROOT.parent / "1-QC3.0.SMP", ROOT.parent / "2-TK500.SMP"])
        window.analysis_tabs.setCurrentIndex(2)
        window.show()
        application.processEvents()
        field = window.pore_calculated_surface_area_edit
        assert window.pore_surface_area_mode_combo.currentIndex() == 1
        for result, analysis in zip(window.results, window.pore_structure_results):
            assert window._pore_options_for_result(result).use_calculated_surface_area
            assert analysis.surface_area_m2g == pytest.approx(summary_metrics(result).total_pore_area)
        for index in (0, 1, 0):
            window.on_active_tab_changed(index)
            expected = summary_metrics(window.results[index]).total_pore_area
            assert field.text() == f"{expected:.4f}"
            window.pore_surface_area_mode_combo.setCurrentIndex(0)
            window.pore_surface_area_spin.setValue(500.0 + index)
            window.pore_surface_area_mode_combo.setCurrentIndex(1)
            assert field.isEnabled()
            assert not window.pore_surface_area_spin.isEnabled()
            assert window.pore_structure_results[index].surface_area_m2g == pytest.approx(expected)
            field.setFocus()
            field.selectAll()
            field.copy()
            assert application.clipboard().text() == f"{expected:.4f}"
            QtTest.QTest.keyClicks(field, "123")
            assert field.text() == f"{expected:.4f}"
            window.pore_surface_area_mode_combo.setCurrentIndex(0)
            assert window.pore_surface_area_spin.isEnabled()
            assert window.pore_surface_area_spin.value() == 500.0 + index
            assert window.pore_structure_results[index].surface_area_m2g == 500.0 + index
    finally:
        window.close()
        application.processEvents()
