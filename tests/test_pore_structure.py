from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path
import sys

import numpy as np
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from mercury_app.core import (
    calculate_pore_structure,
    default_pore_structure_options,
    load_smp,
)


CALIBRATION_FILE = Path(__file__).resolve().parents[2] / "2-TK500.SMP"


@pytest.mark.skipif(not CALIBRATION_FILE.exists(), reason="AutoPore calibration sample is unavailable")
def test_pore_structure_matches_tk500_reference_order_of_magnitude() -> None:
    result = load_smp(CALIBRATION_FILE)
    options = replace(default_pore_structure_options(result), surface_area_m2g=500.0, use_calculated_surface_area=False)
    analysis = calculate_pore_structure(result, options, pressure_min=400.0, pressure_max=10000.0)

    assert analysis.is_valid
    assert analysis.threshold_pressure_psia == pytest.approx(3162.26, rel=1e-5)
    assert analysis.characteristic_length_nm == pytest.approx(57.19, rel=0.02)
    assert analysis.permeability_md == pytest.approx(0.0041, rel=0.12)
    assert analysis.conductivity_formation_factor == pytest.approx(0.281, rel=0.12)
    assert analysis.tortuosity_factor == pytest.approx(1.845, rel=0.01)
    assert analysis.cumulative_percent[-1] == pytest.approx(100.0)
    assert analysis.cumulative_percent[0] == pytest.approx(0.0)
    assert analysis.pressure[[0, -1]] == pytest.approx([400.0, 10000.0])
    assert np.all(np.diff(analysis.pressure) > 0)

    narrowed = calculate_pore_structure(result, options, pressure_min=3500.0, pressure_max=10000.0)
    assert narrowed.threshold_pressure_psia != pytest.approx(analysis.threshold_pressure_psia)
    assert 3500.0 <= narrowed.threshold_pressure_psia <= 10000.0
    assert narrowed.pressure[[0, -1]] == pytest.approx([3500.0, 10000.0])
    assert narrowed.cumulative_percent[[0, -1]] == pytest.approx([0.0, 100.0])
    wider_right = calculate_pore_structure(result, options, pressure_min=400.0, pressure_max=30000.0)
    assert wider_right.threshold_pressure_psia == pytest.approx(analysis.threshold_pressure_psia)


def test_selected_cumulative_percent_uses_interpolated_boundary_volumes() -> None:
    from mercury_app.core.pore_structure import _selected_cumulative_percent

    pressure = np.array([10., 20., 30., 40., 50.])
    volume = np.array([2., 4., 8., 10., 14.])
    x, y = _selected_cumulative_percent(pressure, volume, 15., 45.)
    np.testing.assert_allclose(x, [15., 20., 30., 40., 45.])
    np.testing.assert_allclose(y, np.array([0., 1., 5., 7., 9.]) / 9. * 100.)
    # Reversed bounds, limits outside a sample, and the full range.
    reverse_x, reverse_y = _selected_cumulative_percent(pressure, volume, 45., 15.)
    np.testing.assert_allclose(reverse_x, x)
    np.testing.assert_allclose(reverse_y, y)
    for lo, hi in ((None, None), (1., 100.)):
        full_x, full_y = _selected_cumulative_percent(pressure, volume, lo, hi)
        np.testing.assert_allclose(full_x, pressure)
        np.testing.assert_allclose(full_y, (volume - 2.) / 12. * 100.)
    for lo, hi in ((60., 70.), (20., 20.)):
        empty_x, empty_y = _selected_cumulative_percent(pressure, volume, lo, hi)
        assert empty_x.size == empty_y.size == 0
    empty_x, empty_y = _selected_cumulative_percent(pressure, np.ones(5), 15., 45.)
    assert empty_x.size == empty_y.size == 0


def test_pore_structure_page_and_left_detail_tabs_exist() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from pyqtgraph.Qt import QtWidgets
    from mercury_app.ui.main_window import MainWindow

    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = MainWindow()
    try:
        assert window.analysis_tabs.tabText(2) == "孔结构"
        assert [window.detail_tabs.tabText(i) for i in range(window.detail_tabs.count())] == [
            "样品信息",
            "选区孔容",
            "孔结构",
        ]
        assert window.pore_structure_summary_table.columnCount() == 10
        assert window.pore_structure_summary_table.horizontalHeaderItem(1).text() == "压汞比表面积 (m²/g)"
        assert window.pore_structure_summary_table.horizontalHeaderItem(2).text() == "体积密度 (g/mL)"
        assert window.pore_structure_summary_table.horizontalHeaderItem(3).text() == "骨架密度 (g/mL)"
        assert window.pore_structure_summary_table.horizontalHeaderItem(4).text() == "渗透率 (mdarcy)"
        assert window.pore_structure_summary_table.horizontalHeaderItem(5).text() == "阈值压力 (psia)"
        assert not hasattr(window, "pore_structure_sample_combo")
        assert window.pore_surface_area_spin.buttonSymbols() == QtWidgets.QAbstractSpinBox.NoButtons
        assert window.sample_list.columnCount() == 5
        assert window.pore_structure_summary_table.FROZEN_COLUMN_COUNT == 1
        assert window.pore_structure_summary_table.alternatingRowColors()
        assert window.pore_structure_summary_table._frozen_table.alternatingRowColors()
        assert window.pore_structure_summary_table.showGrid()
        assert window.pore_structure_summary_table._frozen_table.showGrid()
        assert (
            window.pore_structure_summary_table.horizontalHeader().height()
            == window.pore_structure_summary_table.frozen_header().height()
        )
        assert window.selected_pore_volume_table.alternatingRowColors()
        assert window.metrics_stack.widget(0).alternatingRowColors()
        assert not window.pore_structure_summary_table.horizontalHeader().isSortIndicatorShown()
        window._sort_pore_structure_summary(0)
        assert window.pore_structure_sort_column == -1
    finally:
        window.close()
        application.processEvents()
