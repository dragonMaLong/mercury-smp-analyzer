"""Orientation must preserve sample identity, numeric results and header actions."""
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from pyqtgraph.Qt import QtCore, QtGui, QtWidgets, QtTest

from mercury_app.core.export_settings import AppSettings
from mercury_app.ui.main_window import FrozenFirstColumnTableWidget, MainWindow, NumericTableWidgetItem
from mercury_app.ui import pore_table


@pytest.fixture
def table_harness(qt_runtime, tmp_path, monkeypatch):
    table = FrozenFirstColumnTableWidget(0, len(pore_table.PORE_HEADERS))
    table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectItems)
    table._frozen_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectItems)
    table.setHorizontalHeaderLabels(list(pore_table.PORE_HEADERS))
    table.setAttribute(QtCore.Qt.WA_DontShowOnScreen)
    table.resize(600, 360)
    results = [SimpleNamespace(metadata={"file_name": name}, number=number)
               for name, number in (("样品2.SMP", 2), ("样品10.SMP", 10))]
    settings = QtCore.QSettings(str(tmp_path / "settings.ini"), QtCore.QSettings.IniFormat)
    calls = []
    window = SimpleNamespace(
        results=results,
        pore_structure_results=[SimpleNamespace(permeability_md=17.12346, threshold_pressure_psia=400.125),
                                SimpleNamespace(permeability_md=81.0, threshold_pressure_psia=600.0)],
        app_settings=AppSettings("both", tmp_path, samples_as_columns=True), settings=settings,
        active_index=0, _hovered_sample_row=-1,
        _sample_row_label=lambda result: result.metadata["file_name"],
        _set_hovered_sample_row=lambda row: calls.append(("hover", row)),
        _on_sample_table_row_hovered=lambda row: calls.append(("hover", row)),
        _apply_sample_row_hover=lambda row, state: None,
        _sort_pore_structure_summary=lambda column: calls.append(("sort", column)),
        _select_sample_from_curve=lambda row: calls.append(("sample", row)),
    )
    monkeypatch.setattr(pore_table, "summary_metrics", lambda result: SimpleNamespace(
        total_pore_area=result.number * 10.0, bulk_density=result.number / 10.0,
        apparent_density=result.number / 5.0,
    ))
    controller = pore_table.PoreTableController(window, table, NumericTableWidgetItem)
    table.show()
    qt_runtime.processEvents()
    yield window, table, controller, calls
    table.close()
    table.deleteLater()
    qt_runtime.processEvents()


def test_toggle_preserves_values_and_uses_shared_export_setting(table_harness):
    window, table, controller, calls = table_harness
    identities = [id(result) for result in window.results]
    assert (table.rowCount(), table.columnCount()) == (9, 3)
    assert table.horizontalHeaderItem(0).text() == "参数"
    assert table.horizontalHeaderItem(1).text() == "样品2.SMP"
    assert table.item(3, 1).text() == "17.1235"
    assert table.item(4, 1).text() == "400.12"
    assert table.item(5, 1).text() == "—"
    table.selectAll()
    button = table.frozen_header().transpose_button
    QtTest.QTest.mouseClick(button, QtCore.Qt.LeftButton)
    assert (table.rowCount(), table.columnCount()) == (2, 10)
    assert table.horizontalHeaderItem(0).text() == "样品"
    assert table.item(0, 4).text() == "17.1235"
    assert not table.selectedIndexes()
    assert not window.app_settings.samples_as_columns
    assert not AppSettings.from_settings(window.settings).samples_as_columns
    assert all(table._frozen_table.isColumnHidden(col) for col in range(1, 10))
    controller.toggle()
    assert [id(result) for result in window.results] == identities
    assert window.app_settings.samples_as_columns
    assert table.item(3, 1).text() == "17.1235"


def test_transposed_parameter_click_sorts_metric_and_header_selects_sample(table_harness):
    window, table, controller, calls = table_harness
    frozen = table._frozen_table
    position = frozen.visualRect(table.model().index(3, 0)).center()
    QtTest.QTest.mouseClick(frozen.viewport(), QtCore.Qt.LeftButton, pos=position)
    assert ("sort", 4) in calls
    assert not table.selectedIndexes()
    calls.clear()
    header = table.horizontalHeader()
    position = QtCore.QPoint(header.sectionViewportPosition(2) + header.sectionSize(2) // 2, header.height() // 2)
    QtTest.QTest.mouseClick(header.viewport(), QtCore.Qt.LeftButton, pos=position)
    assert ("sample", 1) in calls
    assert not any(name == "sort" for name, _ in calls)
    controller.set_samples_as_columns(False)
    calls.clear()
    controller._on_header_clicked(6)
    assert calls == [("sort", 6)]


def test_sample_mapping_and_frozen_columns_survive_import_and_clear(table_harness):
    window, table, controller, calls = table_harness
    assert controller.sample_index_for_cell(7, 2) == 1
    assert controller.sample_index_for_cell(2, 0) == -1
    assert controller.sample_items(1)[0].text() == "样品10.SMP"
    assert controller.sample_items(1)[4].text() == "81.0000"
    for number in range(3, 12):
        window.results.append(SimpleNamespace(metadata={"file_name": f"S{number}.SMP"}, number=number))
    controller.refresh()
    assert table.columnCount() == 12
    assert all(table._frozen_table.isColumnHidden(col) for col in range(1, 12))
    window.results.clear()
    window.pore_structure_results.clear()
    controller.refresh()
    assert (table.rowCount(), table.columnCount()) == (9, 1)
    assert not controller.sample_items(0)
    controller.set_samples_as_columns(False)
    assert (table.rowCount(), table.columnCount()) == (0, 10)


def test_preference_change_reuses_direction_control(table_harness):
    window, table, controller, calls = table_harness
    window.app_settings = replace(window.app_settings, samples_as_columns=False)
    controller.set_samples_as_columns(window.app_settings.samples_as_columns)
    assert not table._transposed
    assert "作为列" in table.frozen_header().transpose_button.toolTip()
    assert len(controller.sample_items(0)) == 10


def test_main_window_transposed_hover_copy_scroll_sort_and_clear(qt_runtime, capsys):
    source = Path(__file__).resolve().parents[2] / "1-QC3.0.SMP"
    if not source.exists():
        pytest.skip("Local SMP reference file is unavailable")
    window = MainWindow()
    window.setAttribute(QtCore.Qt.WA_DontShowOnScreen)
    try:
        window.load_files([source] * 7)
        for number, result in enumerate(window.results):
            result.metadata["file_name"] = f"样品{number + 1}.SMP"
        window.pore_table_controller.set_samples_as_columns(True)
        window.detail_tabs.setCurrentIndex(2)
        window.show()
        qt_runtime.processEvents()
        table = window.pore_structure_summary_table
        table.horizontalScrollBar().setValue(table.horizontalScrollBar().maximum())
        qt_runtime.processEvents()
        assert table.columnViewportPosition(1) < table._frozen_table.width()
        sample = len(window.results) - 1
        metric_row = 3
        index = table.model().index(metric_row, sample + 1)
        position = table.visualRect(index).center()
        qt_runtime.sendEvent(table.viewport(), QtGui.QMouseEvent(
            QtCore.QEvent.MouseMove, QtCore.QPointF(position), QtCore.Qt.NoButton,
            QtCore.Qt.NoButton, QtCore.Qt.NoModifier,
        ))
        assert window._hovered_sample_row == sample
        assert all(item.font().bold() for item in window.pore_table_controller.sample_items(sample))
        assert window.pressure_plot._sample_curve_interaction_controller.hovered_sample_index == sample
        table.selectionModel().select(index, QtCore.QItemSelectionModel.ClearAndSelect)
        window._copy_pore_structure_selection()
        assert qt_runtime.clipboard().text().splitlines() == [
            "参数\t样品7.SMP", f"渗透率 (mdarcy)\t{table.item(metric_row, sample + 1).text()}",
        ]
        window.update_pore_structure()
        assert all(item.font().bold() for item in window.pore_table_controller.sample_items(sample))
        window.pore_table_controller.toggle()
        assert window._hovered_sample_row == -1
        assert window.pressure_plot._sample_curve_interaction_controller.hovered_sample_index is None
        window.pore_table_controller.toggle()
        window.sort_samples_by_file_name(False)
        assert table.horizontalHeaderItem(1).text() == "样品7.SMP"
        window.clear_sample_list()
        assert (table.rowCount(), table.columnCount()) == (9, 1)
        assert not window.results
        window.load_file(source)
        assert (table.rowCount(), table.columnCount()) == (9, 2)
        window.delete_sample(0)
        assert (table.rowCount(), table.columnCount()) == (9, 1)
    finally:
        qt_runtime.processEvents()
        window.close()
        window.deleteLater()
        qt_runtime.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)
        qt_runtime.processEvents()
    assert "Traceback" not in capsys.readouterr().err
