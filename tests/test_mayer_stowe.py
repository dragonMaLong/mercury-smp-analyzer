from __future__ import annotations

import os
from pathlib import Path
import sys

import numpy as np
import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = PROJECT_ROOT if (PROJECT_ROOT / "1-QC3.0.SMP").exists() else PROJECT_ROOT.parent
sys.path.insert(0, str(PROJECT_ROOT))

from mercury_app.core import calculate_mayer_stowe, load_smp, lookup_mayer_stowe_k
from mercury_app.core.mayer_stowe import (
    MAYER_STOWE_K_TABLE,
    MAYER_STOWE_PACKING_ANGLE_NODES,
    MAYER_STOWE_POROSITY_NODES,
    MAYER_STOWE_THETA_NODES,
)


def test_embedded_mayer_stowe_table_shape_and_axes() -> None:
    assert MAYER_STOWE_K_TABLE.shape == (10, 23)
    assert MAYER_STOWE_THETA_NODES.tolist() == [100, 110, 117, 120, 130, 140, 150, 160, 170, 180]
    assert MAYER_STOWE_PACKING_ANGLE_NODES[[0, -1]].tolist() == [60, 90]
    assert np.all(np.diff(MAYER_STOWE_POROSITY_NODES) > 0)


def test_mayer_stowe_lookup_matches_autopore_reference_points() -> None:
    assert lookup_mayer_stowe_k(130, 0.2595) == pytest.approx(8.32)
    assert lookup_mayer_stowe_k(130, 0.4763) == pytest.approx(3.3512406155816854)
    assert lookup_mayer_stowe_k(140, 0.2595) == pytest.approx(9.46)


def test_mayer_stowe_lookup_interpolates_both_dimensions() -> None:
    epsilon = float((MAYER_STOWE_POROSITY_NODES[10] + MAYER_STOWE_POROSITY_NODES[11]) / 2.0)
    expected_at_130 = (7.04 + 6.86) / 2.0
    expected_at_140 = (8.15 + 7.95) / 2.0
    expected = (expected_at_130 + expected_at_140) / 2.0
    assert lookup_mayer_stowe_k(135, epsilon) == pytest.approx(expected)
    assert np.isnan(lookup_mayer_stowe_k(99.9, epsilon))


@pytest.mark.skipif(
    not (DATA_ROOT / "1-QC3.0.SMP").exists(),
    reason="Local SMP reference file is not included in the source repository.",
)
def test_mayer_stowe_distribution_uses_selected_pressure_range() -> None:
    result = load_smp(DATA_ROOT / "1-QC3.0.SMP")
    analysis = calculate_mayer_stowe(result, 1000.0, result.max_pressure)

    assert analysis.is_valid
    assert analysis.pressure[0] == pytest.approx(1000.0)
    assert analysis.pressure[-1] == pytest.approx(result.max_pressure)
    assert analysis.cumulative_coarser_percent[0] == pytest.approx(0.0)
    assert analysis.cumulative_coarser_percent[-1] == pytest.approx(100.0)
    assert analysis.incremental_percent[0] == pytest.approx(0.0)
    assert np.sum(analysis.incremental_percent) == pytest.approx(100.0)
    assert np.all(np.diff(analysis.particle_diameter) < 0)


def test_main_window_has_analysis_tabs() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from pyqtgraph.Qt import QtWidgets
    from mercury_app.ui.main_window import MainWindow

    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = MainWindow()
    try:
        assert window.analysis_tabs.count() == 3
        assert [window.analysis_tabs.tabText(i) for i in range(3)] == ["孔径分布", "Mayer Stowe", "孔结构"]
        assert window.analysis_tabs.widget(0) is window.plot_splitter
        assert window.analysis_tabs.documentMode() is False
        assert window.analysis_tabs.styleSheet() == ""
        assert [window.mayer_stowe_plot_tabs.tabText(i) for i in range(2)] == [
            "增量体积分布",
            "累计体积",
        ]
        assert window.mayer_stowe_plot_tabs.widget(0) is window.mayer_stowe_incremental_plot
        assert window.mayer_stowe_plot_tabs.widget(1) is window.mayer_stowe_cumulative_plot
        assert window.mayer_stowe_splitter.widget(0) is window.mayer_stowe_plot_tabs
        assert window.mayer_stowe_splitter.widget(1) is window.mayer_pressure_plot
    finally:
        window.close()
        application.processEvents()


@pytest.mark.skipif(
    not (DATA_ROOT / "1-QC3.0.SMP").exists(),
    reason="Local SMP reference file is not included in the source repository.",
)
def test_pressure_regions_are_independent_and_use_requested_defaults() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from pyqtgraph.Qt import QtTest, QtWidgets
    from mercury_app.ui.main_window import MainWindow

    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = MainWindow()
    try:
        window.load_file(DATA_ROOT / "1-QC3.0.SMP")
        application.processEvents()

        incremental_items = window.mayer_stowe_incremental_plot.listDataItems()
        cumulative_items = window.mayer_stowe_cumulative_plot.listDataItems()
        raw_point_count = window.mayer_stowe_results[0].particle_diameter.size
        assert any(len(item.xData) > raw_point_count for item in incremental_items)
        assert any(len(item.xData) == raw_point_count for item in incremental_items)
        assert any(len(item.xData) > raw_point_count for item in cumulative_items)
        assert any(len(item.xData) == raw_point_count for item in cumulative_items)
        smooth_cumulative = next(item.yData for item in cumulative_items if len(item.xData) > raw_point_count)
        assert np.nanmin(smooth_cumulative) >= 0.0
        assert np.nanmax(smooth_cumulative) <= 100.0

        assert window._current_pressure_region() == pytest.approx([2900.0, 9100.0])
        assert window._current_mayer_pressure_region() == pytest.approx([400.0, 10000.0])
        assert [label.toPlainText() for label in window.pressure_region_labels] == ["2,900", "9,100"]
        assert [label.toPlainText() for label in window.mayer_pressure_region_labels] == ["400", "10,000"]

        window.region.setRegion(window._pressure_to_region_values(3200.0, 8800.0))
        application.processEvents()
        assert window._current_pressure_region() == pytest.approx([3200.0, 8800.0])
        assert window._current_mayer_pressure_region() == pytest.approx([400.0, 10000.0])

        window.mayer_pressure_region.setRegion(window._mayer_pressure_to_region_values(500.0, 9000.0))
        QtTest.QTest.qWait(50)
        assert window._current_pressure_region() == pytest.approx([3200.0, 8800.0])
        assert window._current_mayer_pressure_region() == pytest.approx([500.0, 9000.0])
        assert window.mayer_stowe_results[0].pressure_min == pytest.approx(500.0)
        assert window.mayer_stowe_results[0].pressure_max == pytest.approx(9000.0)
    finally:
        window.close()
        application.processEvents()


@pytest.mark.skipif(
    not (DATA_ROOT / "1-QC3.0.SMP").exists(),
    reason="Local SMP reference file is not included in the source repository.",
)
def test_blue_pressure_boundary_value_can_be_edited() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from pyqtgraph.Qt import QtWidgets
    from mercury_app.ui.main_window import MainWindow

    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = MainWindow()
    try:
        window.load_file(DATA_ROOT / "1-QC3.0.SMP")
        application.processEvents()
        window._begin_pressure_region_endpoint_edit(0)
        window.pressure_region_editor.setText("3100")
        window._mark_pressure_region_editor_dirty("3100")
        window._commit_pressure_region_endpoint_edit_for(mayer=False)
        application.processEvents()
        assert window._current_pressure_region() == pytest.approx([3100.0, 9100.0])
    finally:
        window.close()
        application.processEvents()
