"""The "分析结果" tab lists per-sample results and follows the selected range."""
from __future__ import annotations

import numpy as np
import pytest

from mercury_app.core import summary_metrics
from mercury_app.core.metrics import metrics_for_pressure_range
from mercury_app.core.models import MercuryResult
from mercury_app.ui.analysis_table import ANALYSIS_HEADERS, SELECTION_START, analysis_values


def _result(name: str, scale: float) -> MercuryResult:
    pressure = np.array([1., 10., 100., 1000., 10000.])
    volume = scale * np.array([0.0, 0.05, 0.2, 0.35, 0.4])
    rows = [
        {
            "pressure": p,
            "diameter": 180000. / p,
            "cum_vol": v,
            "incr_vol": i,
            "log_diff": i,
            "pct_total": float("nan"),
            "pct_incr": float("nan"),
        }
        for p, v, i in zip(pressure, volume, np.diff(volume, prepend=0.))
    ]
    return MercuryResult.from_rows(
        {
            "file_name": name,
            "sample_name": name,
            "sample_mass_g": 1.,
            "adv_contact_angle_deg": 130.,
            "surface_tension_dynes_cm": 485.,
            "penetrometer_bulb_volume_mL": 2.,
            "mercury_mass_g": 13.5,
            "mercury_density_gmL": 13.5,
        },
        rows,
    )


def test_values_pair_each_result_with_its_pressure_row():
    result = _result("A.SMP", 1.)
    summary = summary_metrics(result)
    segment = metrics_for_pressure_range(result, 10., 1000.)
    values = dict(zip(ANALYSIS_HEADERS[1:], analysis_values(result, segment)))

    assert len(values) == len(ANALYSIS_HEADERS) - 1
    names = list(values)
    assert not any("对应" in name for name in names)
    assert values["总入汞体积 (mL/g)"] == (pytest.approx(summary.total_intrusion_volume), "0.4")
    assert values["最大压力 (psia)"][1] == "10000"
    assert values["孔隙率 (%)"][0] == pytest.approx(summary.porosity)
    assert values["压力范围 (psia)"][1] == "10 – 1000"
    assert values["孔容 (mL/g)"][0] == pytest.approx(segment.pore_volume)
    assert names[:2] == ["进汞接触角 (°)", "表面张力 (dynes/cm)"]
    assert (values["进汞接触角 (°)"], values["表面张力 (dynes/cm)"]) == ((130., "130"), (485., "485"))
    assert "选区数据点数" not in values

    without_range = analysis_values(result)
    assert without_range[:SELECTION_START - 1] == analysis_values(result, segment)[:SELECTION_START - 1]
    assert all(text == "—" for _, text in without_range[SELECTION_START - 1:])


def test_tab_follows_selection_orientation_and_sorting(qt_runtime):
    from mercury_app.ui.main_window import MainWindow, QtCore

    window = MainWindow()
    try:
        assert window.detail_tabs.tabText(1) == "分析结果"
        assert window.detail_tabs.widget(1) is window.analysis_result_table
        window.results = [_result("B.SMP", 2.), _result("A.SMP", 1.)]
        window.visible_results = [True, True]
        window._build_metric_tabs()
        table = window.analysis_result_table
        controller = window.analysis_table_controller
        controller.set_samples_as_columns(True)
        assert window.pore_table_controller.samples_as_columns

        def column(sample):
            return {name: table.item(row, sample + 1).text() for row, name in enumerate(ANALYSIS_HEADERS[1:])}

        window._refresh_all_metric_tables(10., 1000.)
        assert [table.horizontalHeaderItem(i).text() for i in range(3)] == ["参数", "B.SMP", "A.SMP"]
        assert table.rowCount() == len(ANALYSIS_HEADERS) - 1
        assert [table.item(row, 0).text() for row in range(table.rowCount())] == list(ANALYSIS_HEADERS[1:])
        assert column(0)["总入汞体积 (mL/g)"] == "0.8"
        assert column(1)["压力范围 (psia)"] == "10 – 1000"
        wide = column(1)["孔容 (mL/g)"]
        window._refresh_all_metric_tables(10., 100.)
        assert column(1)["压力范围 (psia)"] == "10 – 100"
        assert float(column(1)["孔容 (mL/g)"]) < float(wide)
        assert column(1)["总入汞体积 (mL/g)"] == "0.4"

        # Results moved out of the sample information table.
        info = window.metric_tables[0]
        info_names = [info.item(row, 0).text() for row in range(info.rowCount())]
        assert "样品质量" in info_names and "数据点数" in info_names
        assert not {"孔隙率", "总入汞体积", "最大压力", "孔容", "压力范围", "进汞接触角", "表面张力"} & set(info_names)

        # Either result table switches both; the pore-structure table keeps its rows.
        window.pore_table_controller.toggle()
        assert not controller.samples_as_columns
        assert (table.rowCount(), table.columnCount()) == (2, len(ANALYSIS_HEADERS))
        assert table.horizontalHeaderItem(3).text() == "总入汞体积 (mL/g)"
        assert table.item(1, 3).text() == "0.4"
        assert len(controller.sample_items(1)) == len(ANALYSIS_HEADERS)

        window._sort_analysis_results(3)
        assert [result.metadata["file_name"] for result in window.results] == ["A.SMP", "B.SMP"]
        window._sort_analysis_results(3)
        assert [result.metadata["file_name"] for result in window.results] == ["B.SMP", "A.SMP"]
        assert table.item(0, 0).text() == "B.SMP"

        index = table.model().index(1, 3)
        table.selectionModel().select(index, QtCore.QItemSelectionModel.ClearAndSelect)
        window._copy_pore_structure_selection(table)
        assert qt_runtime.clipboard().text().splitlines() == ["样品\t总入汞体积 (mL/g)", "A.SMP\t0.4"]
    finally:
        window.close()
        qt_runtime.processEvents()


def test_frozen_parameter_names_follow_vertical_scrolling(qt_runtime):
    from mercury_app.ui.main_window import MainWindow

    window = MainWindow()
    try:
        window.results = [_result("A.SMP", 1.), _result("B.SMP", 2.)]
        window.visible_results = [True, True]
        window._build_metric_tabs()
        window.analysis_table_controller.set_samples_as_columns(True)
        window._refresh_all_metric_tables(10., 1000.)
        window.detail_tabs.setCurrentIndex(1)
        window.resize(800, 537)
        window.show()
        qt_runtime.processEvents()
        table = window.analysis_result_table
        frozen = table._frozen_table
        assert table.verticalScrollBar().maximum() > 0
        # Scroll from either side, including the bottom, and compare displayed
        # row positions rather than scrollbar units or implementation settings.
        for view in (table, frozen):
            for value in (view.verticalScrollBar().maximum() // 2,
                          view.verticalScrollBar().maximum(), 0):
                view.verticalScrollBar().setValue(value)
                qt_runtime.processEvents()
                assert table.rowAt(10) == frozen.rowAt(10)
                for row in range(table.rowCount()):
                    assert table.rowViewportPosition(row) == frozen.rowViewportPosition(row)
    finally:
        window.close()
        qt_runtime.processEvents()
