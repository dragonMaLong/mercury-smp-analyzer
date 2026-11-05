from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from pyqtgraph.Qt import QtCore, QtTest, QtWidgets

from mercury_app.ui import main_window
from mercury_app.ui.main_window import FileImportDialog, SUPPORTED_SMP_SUFFIXES
from mercury_app.ui.native_import_picker import ImportSelection


@pytest.fixture
def importer(tmp_path, qt_runtime):
    first, second = tmp_path / "first", tmp_path / "中文文件夹"
    first.mkdir()
    second.mkdir()
    for folder, names in ((first, ("existing.SMP", "other.smp")),
                          (second, ("new.SMP", "extra.smp", "unsupported.xlsx"))):
        for name in names:
            (folder / name).write_text("picker fixture", encoding="utf-8")
    dialog = FileImportDialog(initial_dir=first, existing_paths=[str(first / "existing.SMP")])
    yield SimpleNamespace(dialog=dialog, first=first, second=second)
    dialog.close()
    dialog.deleteLater()
    QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)


def table_paths(table):
    return [table.item(row, 0).data(QtCore.Qt.UserRole) for row in range(table.rowCount())]


def choose_native(monkeypatch, dialog, selection):
    picker = Mock(return_value=selection)
    monkeypatch.setattr(main_window, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(main_window, "choose_import_files", picker)
    monkeypatch.setattr(main_window, "ImportFilePicker", Mock(side_effect=AssertionError("Unexpected Qt fallback")))
    dialog._browse_directory()
    return picker


def snapshot(dialog):
    return (dialog.current_directory, dialog.folder_edit.text(), dialog.search_edit.text(),
            table_paths(dialog.available_table), dialog.selected_paths(), dialog.available_sort(),
            dialog.count_label.text(), dialog.result())


def test_toolbar_routes_to_native_file_and_folder_picker(importer, monkeypatch):
    dialog = importer.dialog
    assert [button.text() for button in dialog.findChildren(QtWidgets.QToolButton)] == ["选择文件夹", "刷新"]
    picker = choose_native(monkeypatch, dialog, None)
    owner, directory, suffixes = picker.call_args.args
    assert owner != 0 and directory == importer.first
    assert suffixes == SUPPORTED_SMP_SUFFIXES
    assert dialog.import_button.text() == "开始导入"


def test_selecting_folder_updates_available_list_and_keeps_staged_files(importer, monkeypatch):
    dialog = importer.dialog
    accepted = QtTest.QSignalSpy(dialog.accepted)
    dialog.search_edit.setText("other")
    choose_native(monkeypatch, dialog, ImportSelection(importer.second))
    assert dialog.current_directory == importer.second
    assert dialog.folder_edit.text() == str(importer.second)
    assert dialog.search_edit.text() == ""
    assert set(table_paths(dialog.available_table)) == {str(importer.second / name) for name in ("new.SMP", "extra.smp")}
    assert dialog.selected_paths() == [str(importer.first / "existing.SMP")]
    assert len(accepted) == 0


def test_add_multiple_files_deduplicates_and_waits_for_start_import(importer, monkeypatch):
    dialog = importer.dialog
    accepted = QtTest.QSignalSpy(dialog.accepted)
    new, extra = importer.second / "new.SMP", importer.second / "extra.smp"
    old, other = importer.first / "existing.SMP", importer.first / "other.smp"
    choose_native(monkeypatch, dialog, ImportSelection(importer.second, (new, extra)))
    choose_native(monkeypatch, dialog, ImportSelection(importer.first, (old, other, new)))
    expected = [str(path) for path in (old, new, extra, other)]
    assert dialog.selected_paths() == expected
    assert table_paths(dialog.selected_table) == expected
    assert not table_paths(dialog.available_table)
    assert len(accepted) == 0
    dialog.import_button.click()
    assert len(accepted) == 1


def test_cancel_preserves_directory_filter_sort_and_both_lists(importer, monkeypatch):
    dialog = importer.dialog
    dialog.search_edit.setText("other")
    dialog.available_table.sortItems(1, QtCore.Qt.DescendingOrder)
    before = snapshot(dialog)
    choose_native(monkeypatch, dialog, None)
    assert snapshot(dialog) == before


def test_native_failure_reports_error_without_changing_state(importer, monkeypatch):
    dialog = importer.dialog
    before = snapshot(dialog)
    monkeypatch.setattr(main_window, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(main_window, "choose_import_files", Mock(side_effect=OSError("picker unavailable")))
    warning = Mock()
    monkeypatch.setattr(QtWidgets.QMessageBox, "warning", warning)
    dialog._browse_directory()
    warning.assert_called_once()
    assert snapshot(dialog) == before


def test_manual_file_selection_rejects_missing_and_unsupported_files(importer, monkeypatch):
    new = importer.second / "new.SMP"
    invalid = (importer.second / "unsupported.xlsx", importer.second / "missing.SMP")
    warning = Mock()
    monkeypatch.setattr(QtWidgets.QMessageBox, "warning", warning)
    choose_native(monkeypatch, importer.dialog, ImportSelection(importer.second, (new, *invalid)))
    assert importer.dialog.selected_paths() == [str(importer.first / "existing.SMP"), str(new)]
    warning.assert_called_once()
    assert all(str(path) in warning.call_args.args[-1] for path in invalid)


@pytest.mark.parametrize("folder_only", [True, False])
def test_non_windows_picker_matches_native_staging_behavior(importer, monkeypatch, folder_only):
    dialog = importer.dialog
    picker = Mock()
    picker.exec_.return_value = QtWidgets.QDialog.Accepted
    picker.chosen_directory = importer.second if folder_only else None
    picker.selectedFiles.return_value = [str(importer.second / "new.SMP")]
    monkeypatch.setattr(main_window, "sys", SimpleNamespace(platform="linux"))
    monkeypatch.setattr(main_window, "ImportFilePicker", Mock(return_value=picker))
    monkeypatch.setattr(main_window, "choose_import_files", Mock(side_effect=AssertionError("Unexpected native picker")))
    dialog._browse_directory()
    assert dialog.current_directory == importer.second
    assert dialog.selected_paths() == [str(importer.first / "existing.SMP")] + (
        [] if folder_only else [str(importer.second / "new.SMP")])
    picker.deleteLater.assert_called_once()
