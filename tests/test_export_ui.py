from dataclasses import replace
from pathlib import Path

import pytest

from mercury_app.ui.main_window import MainWindow, QtWidgets


@pytest.fixture
def export_window(qt_runtime):
    source = Path(__file__).resolve().parents[2] / "1-QC3.0.SMP"
    if not source.exists():
        pytest.skip("Local SMP reference file is unavailable")
    window = MainWindow()
    window.load_files([source, source])
    window.results[0].metadata["file_name"] = "selected-2.SMP"
    window.results[1].metadata["file_name"] = "hidden-10.SMP"
    window.visible_results = [True, False]
    yield window
    window.close()
    qt_runtime.processEvents()


@pytest.mark.parametrize("file_format", ["xlsx", "pdf"])
def test_toolbar_exports_checked_samples_and_remembers_directory(export_window, tmp_path, monkeypatch, file_format):
    window = export_window
    output_stem = tmp_path / "导出结果"
    events, errors, calls = [], [], []
    window._record_telemetry = lambda name, **data: events.append((name, data))
    window.app_settings = replace(window.app_settings, export_format=file_format)

    def choose_file(*args):
        calls.append(args)
        return str(output_stem), "PDF 报告 (*.pdf)" if file_format == "pdf" else "Excel 工作簿 (*.xlsx)"

    monkeypatch.setattr(QtWidgets.QFileDialog, "getSaveFileName", choose_file)
    monkeypatch.setattr(QtWidgets.QMessageBox, "information", lambda *args: None)
    monkeypatch.setattr(QtWidgets.QMessageBox, "critical", lambda *args: errors.append(args[-1]))
    window.export_xls()
    assert errors == []
    output = output_stem.with_suffix("." + file_format)
    assert output.is_file()
    assert window.app_settings.export_directory == tmp_path
    assert window.settings.value("export_directory") == str(tmp_path)
    assert calls[0][2].endswith("." + file_format)
    assert events[-1][1]["format"] == file_format
    assert events[-1][1]["status"] == "success"
    assert QtWidgets.QApplication.overrideCursor() is None
    if file_format == "xlsx":
        import openpyxl
        workbook = openpyxl.load_workbook(output)
        try:
            assert "孔结构" in workbook.sheetnames
            assert "孔结构计算参数" in workbook.sheetnames
            assert "selected-2.SMP" in workbook.sheetnames
            assert "hidden-10.SMP" not in workbook.sheetnames
        finally:
            workbook.close()
    else:
        from pypdf import PdfReader
        reader = PdfReader(output)
        content = "\n".join(page.extract_text() for page in reader.pages)
        assert "selected-2.SMP" in content
        assert "hidden-10.SMP" not in content
        assert "孔结构" in content
        assert len(reader.pages) >= 2


def test_failed_export_keeps_existing_output_and_restores_cursor(export_window, tmp_path, monkeypatch):
    from mercury_app.core import pdf_export

    window = export_window
    output = tmp_path / "already-present.pdf"
    output.write_bytes(b"existing report")
    events, errors = [], []
    window._record_telemetry = lambda name, **data: events.append((name, data))
    monkeypatch.setattr(QtWidgets.QFileDialog, "getSaveFileName", lambda *args: (str(output), "PDF 报告 (*.pdf)"))
    monkeypatch.setattr(QtWidgets.QMessageBox, "critical", lambda *args: errors.append(args[-1]))

    def fail(*args, **kwargs):
        raise OSError("test write failure")

    monkeypatch.setattr(pdf_export, "write_pdf", fail)
    window.export_xls()
    assert errors == ["test write failure"]
    assert output.read_bytes() == b"existing report"
    assert QtWidgets.QApplication.overrideCursor() is None
    assert events[-1][1]["status"] == "failure"


def test_settings_dialog_updates_result_orientation(export_window, monkeypatch, tmp_path):
    from mercury_app.ui import main_window

    window = export_window

    class AcceptedDialog:
        def __init__(self, *args, **kwargs):
            assert kwargs["import_directory"] == window.import_directory

        def exec_(self):
            return QtWidgets.QDialog.Accepted

        def values(self):
            return replace(window.app_settings, export_format="pdf", export_directory=tmp_path,
                           samples_as_columns=not window.pore_table_controller.samples_as_columns)

    previous = window.pore_table_controller.samples_as_columns
    monkeypatch.setattr(main_window, "AppSettingsDialog", AcceptedDialog)
    window.show_app_settings()
    assert window.pore_table_controller.samples_as_columns is not previous
    assert window.app_settings.export_format == "pdf"
    assert window.app_settings.export_directory == tmp_path


def test_added_suffix_does_not_silently_overwrite_an_existing_file(export_window, monkeypatch, tmp_path):
    stem = tmp_path / "existing"
    target = stem.with_suffix(".xlsx")
    target.write_bytes(b"existing workbook")
    questions = []
    monkeypatch.setattr(QtWidgets.QFileDialog, "getSaveFileName", lambda *args: (str(stem), "Excel 工作簿 (*.xlsx)"))

    def decline(*args):
        questions.append(args[2])
        return QtWidgets.QMessageBox.No

    monkeypatch.setattr(QtWidgets.QMessageBox, "question", decline)
    export_window.export_xls()
    assert target.read_bytes() == b"existing workbook"
    assert len(questions) == 1
    assert str(target) in questions[0]
