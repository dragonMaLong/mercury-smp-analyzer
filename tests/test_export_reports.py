from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from mercury_app.core import MercuryResult, default_pore_structure_options
from mercury_app.core.excel_export import export_results_xlsx
from mercury_app.core.export_settings import AppSettings
from mercury_app.core.pdf_export import Table, build_report_from_window, write_pdf


def sample(name="中文样品2.SMP"):
    pressures = np.geomspace(100, 20000, 25)
    volumes = .3 / (1 + np.exp(-3 * (np.log10(pressures) - 3.3)))
    increments = np.diff(volumes, prepend=0)
    return MercuryResult.from_rows({"file_name": name, "sample_name": name[:-4],
        "created": "2026-11-05 08:30", "sample_mass_g": .45,
        "adv_contact_angle_deg": 130, "surface_tension_dynes_cm": 485,
        "bulk_density_gmL": 1.2, "true_density_gmL": 2.5},
        [{"pressure": p, "diameter": 180000 / p, "cum_vol": v, "incr_vol": dv,
          "log_diff_intrusion_smooth": dv * 10} for p, v, dv in zip(pressures, volumes, increments)])


def snapshot_window(results):
    options = {id(r): replace(default_pore_structure_options(r), use_entered_threshold_pressure=True,
                             threshold_pressure_psia=1800, use_calculated_surface_area=False, surface_area_m2g=24.5)
               for r in results}
    return SimpleNamespace(results=results, sample_colors=["#2563eb", "#dc2626", "#16a34a"],
        _sample_row_label=lambda result: result.metadata["file_name"],
        _current_pressure_region=lambda: [500, 8000],
        _current_mayer_pressure_region=lambda: [400, 10000],
        _current_pore_structure_pressure_region=lambda: [600, 12000],
        _pore_options_for_result=lambda result: options[id(result)])


def test_snapshot_uses_selected_order_and_current_inputs():
    first, second = sample("样品2.SMP"), sample("样品10.SMP")
    window = snapshot_window([first, second])
    report = build_report_from_window(window, [second], "1.2.0", include_figures=False)
    assert not report.figures
    assert all([name for name, _ in table.columns] == ["样品10.SMP"] for table in report.tables)
    pore = next(table for table in report.tables if table.title == "孔结构")
    assert float(pore.columns[0][1][pore.row_labels.index("阈值压力 (psia)")]) == 1800
    inputs = next(table for table in report.tables if table.title == "孔结构计算参数")
    assert inputs.columns[0][1][:4] == ["600", "12000", "BET / 手动输入", "24.5"]
    assert "500 - 8000" in report.tables[2].columns[0][1][0]


@pytest.mark.parametrize("samples_as_columns", [True, False])
def test_xlsx_contains_current_analysis_and_numeric_cells(tmp_path, samples_as_columns):
    from openpyxl import load_workbook
    result = sample("=literal.SMP")
    report = build_report_from_window(snapshot_window([result]), [result], include_figures=False)
    path = export_results_xlsx([result], tmp_path / "report.xlsx", result_tables=report.tables[2:],
                               samples_as_columns=samples_as_columns)
    workbook = load_workbook(path)
    assert {"孔结构", "选区孔容", "Mayer-Stowe", "孔结构计算参数"} <= set(workbook.sheetnames)
    assert workbook["汇总"]["A6"].value == "=literal.SMP"
    assert workbook["汇总"]["A6"].data_type == "s"
    table = workbook["孔结构计算参数"]
    assert table["A1"].value == ("项目" if samples_as_columns else "样品")
    assert table["B2"].value == 600
    assert table["B2"].data_type == "n"
    assert table["B3" if samples_as_columns else "C2"].value == 12000
    workbook.close()


def test_failed_excel_export_preserves_existing_file(tmp_path, monkeypatch):
    import openpyxl
    output = tmp_path / "existing.xlsx"
    output.write_bytes(b"existing workbook")
    def fail(*args):
        raise OSError("simulated full disk")
    monkeypatch.setattr(openpyxl.Workbook, "save", fail)
    with pytest.raises(OSError, match="full disk"):
        export_results_xlsx([sample()], output)
    assert output.read_bytes() == b"existing workbook"
    assert list(tmp_path.iterdir()) == [output]


def test_pdf_multiple_samples_chinese_text_and_footer(tmp_path, qt_runtime):
    pypdf = pytest.importorskip("pypdf")
    results = [sample(f"中文样品{i}.SMP") for i in range(5)]
    report = build_report_from_window(snapshot_window(results), results, "1.2.0")
    output = tmp_path / "report.pdf"
    assert write_pdf(report, output) >= 3
    reader = pypdf.PdfReader(output)
    text = "".join(page.extract_text() for page in reader.pages)
    assert "中文样品4" in text
    assert "孔结构" in text
    assert "DragonScience" in (reader.metadata.creator or "")
    for index, page in enumerate(reader.pages, 1):
        assert f"{index} / {len(reader.pages)}" in " ".join(page.extract_text().split())


def test_failed_pdf_preserves_existing_file(tmp_path, qt_runtime, monkeypatch):
    import mercury_app.core.pdf_export as pdf
    output = tmp_path / "existing.pdf"
    output.write_bytes(b"existing pdf")
    def fail(*args):
        raise OSError("simulated full disk")
    monkeypatch.setattr(pdf, "_write_pdf", fail)
    with pytest.raises(OSError, match="full disk"):
        write_pdf(None, output)
    assert output.read_bytes() == b"existing pdf"
    assert list(tmp_path.iterdir()) == [output]


def test_log_figure_points_and_curve_share_coordinates(qt_runtime, monkeypatch):
    import pyqtgraph as pg
    from mercury_app.core.pdf_export import Figure, Series, render_figure
    captured = []
    original = pg.PlotWidget.grab
    def capture(widget, *args):
        for item in widget.getPlotItem().listDataItems():
            captured.append(item.getData()[0])
        return original(widget, *args)
    monkeypatch.setattr(pg.PlotWidget, "grab", capture)
    figure = Figure("Log curve", "x", "y", [Series("points", "#123456", [1, 10, 100], [2, 3, 4], "points"),
        Series("", "#123456", [1, 10, 100], [2, 3, 4], "fit")], log_x=True)
    assert render_figure(figure) is not None
    assert len(captured) == 2
    for xs in captured:
        np.testing.assert_allclose(xs, [0, 1, 2])


def test_export_settings_round_trip(tmp_path, qt_runtime):
    from pyqtgraph.Qt import QtCore
    settings = QtCore.QSettings(str(tmp_path / "prefs.ini"), QtCore.QSettings.IniFormat)
    assert AppSettings.from_settings(settings).samples_as_columns
    expected = AppSettings("pdf", tmp_path, False)
    expected.save(settings)
    settings.sync()
    assert AppSettings.from_settings(settings) == expected
    settings.setValue("export/default_format", "unsupported")
    assert AppSettings.from_settings(settings).export_format == "xlsx"
