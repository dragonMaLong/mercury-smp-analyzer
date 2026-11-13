from __future__ import annotations

import numpy as np
import pytest

from mercury_app.core.models import MercuryResult
from mercury_app.ui.analysis_table import ANALYSIS_HEADERS
from mercury_app.core.pore_structure import PoreStructureOptions, calculate_pore_structure


def _result(total_volume: float) -> MercuryResult:
    pressure = np.array([10., 20., 30., 40., 50.])
    volume = np.linspace(total_volume - 0.4, total_volume, pressure.size)
    rows = [
        {
            "pressure": p,
            "diameter": 10000. / p,
            "cum_vol": v,
            "incr_vol": 0.1,
            "pct_total": float("nan"),
            "pct_incr": float("nan"),
        }
        for p, v in zip(pressure, volume)
    ]
    return MercuryResult.from_rows(
        {
            "sample_name": "Signed legacy volume",
            "sample_mass_g": 1.,
            "penetrometer_bulb_volume_mL": 2.,
            "mercury_mass_g": 13.5,
            "mercury_density_gmL": 13.5,
            "measurement_basis": "intrusion_volume_mL",
            "warnings": ["旧版文件保留原始进汞体积。", "总进汞量≤0，孔隙率不可计算。"],
        },
        rows,
    )


@pytest.mark.parametrize("total_volume", [-0.1, 0.])
@pytest.mark.parametrize("use_entered_factor", [False, True])
def test_nonpositive_volume_cannot_produce_pore_structure_metrics(total_volume, use_entered_factor):
    result = _result(total_volume)
    original_volume = result.cum_volume.copy()
    options = PoreStructureOptions(
        use_entered_density=True,
        bulk_density_gmL=1.,
        skeletal_density_gmL=2.,
        use_entered_conductivity_factor=use_entered_factor,
        use_entered_threshold_pressure=True,
        threshold_pressure_psia=20.,
    )

    analysis = calculate_pore_structure(result, options)

    assert not analysis.is_valid
    assert analysis.cumulative_percent.size == 0
    assert "总进汞量≤0" in analysis.warning
    for name in (
        "permeability_md", "threshold_pressure_psia", "characteristic_length_nm",
        "conductivity_formation_factor", "tortuosity_factor", "tortuosity",
    ):
        assert np.isnan(getattr(analysis, name))
    np.testing.assert_array_equal(result.cum_volume, original_volume)


def test_invalid_summary_shows_dashes_without_warning_row(qt_runtime):
    from mercury_app.ui.main_window import MainWindow

    result = _result(-0.1)
    window = MainWindow()
    try:
        window.results = [result]
        window.visible_results = [True]
        table = window._make_metric_table()
        table.setParent(window)
        window.metric_tables = [table]
        window._refresh_all_metric_tables(10., 50.)

        values = {table.item(row, 0).text(): table.item(row, 1) for row in range(table.rowCount())}
        # The sample information table does not list data warnings or results.
        assert result.metadata["warnings"]
        assert "数据警告" not in values
        assert table.item(0, 0).text() == "文件"
        assert "孔隙率" not in values and "总入汞体积" not in values
        assert all("nan" not in item.text().lower() for item in values.values())

        window.analysis_table_controller.set_samples_as_columns(True)
        results = window.analysis_result_table
        values = {name: results.item(row, 1).text() for row, name in enumerate(ANALYSIS_HEADERS[1:])}
        assert values["孔隙率 (%)"] == "—"
        assert values["表观骨架密度 (g/mL)"] == "—"
        assert values["总入汞体积 (mL/g)"] == "-0.1"
        assert values["最大压力 (psia)"] == "50"
        assert not any("对应" in name for name in values)
        assert all("nan" not in text.lower() for text in values.values())
    finally:
        window.close()
        qt_runtime.processEvents()
