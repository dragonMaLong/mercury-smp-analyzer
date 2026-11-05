"""Exercise the portable picker without touching desktop or user settings."""

from pathlib import Path

import pytest
from PyQt5 import QtTest
from pyqtgraph.Qt import QtCore, QtWidgets

from mercury_app.ui.import_picker import ImportFilePicker


@pytest.fixture
def picker_factory(tmp_path, qt_runtime):
    # QFileDialog's C++ persistence bypasses the Python QSettings patch in
    # conftest, so redirect its native calls as well for each dialog's lifetime.
    settings_type = next(cls for cls in QtCore.QSettings.__mro__ if cls.__module__ == "PyQt5.QtCore")
    original_format = settings_type.defaultFormat()
    scopes = (settings_type.UserScope, settings_type.SystemScope)
    original_paths = {
        scope: str(Path(settings_type(settings_type.IniFormat, scope, "QtProject").fileName()).parent)
        for scope in scopes
    }
    settings_type.setDefaultFormat(settings_type.IniFormat)
    for scope in scopes:
        settings_type.setPath(settings_type.IniFormat, scope, str(tmp_path / "qt-settings"))
    dialogs = []

    def create(directory):
        dialog = ImportFilePicker(initial_dir=directory)
        dialogs.append(dialog)
        return dialog

    yield create
    for dialog in dialogs:
        dialog.close()
        dialog.deleteLater()
    QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)
    qt_runtime.processEvents()
    for scope, path in original_paths.items():
        settings_type.setPath(settings_type.IniFormat, scope, path)
    settings_type.setDefaultFormat(original_format)


def _wait_until(predicate):
    timer = QtCore.QElapsedTimer()
    timer.start()
    while not predicate() and timer.elapsed() < 2000:
        QtTest.QTest.qWait(10)
    assert predicate()


def _button(dialog, text):
    box = dialog.findChild(QtWidgets.QDialogButtonBox)
    return next(button for button in box.buttons() if button.text() == text)


def _visible_names(dialog):
    tree = dialog.findChild(QtWidgets.QTreeView, "treeView")
    model = tree.model()
    return {model.index(row, 0, tree.rootIndex()).data() for row in range(model.rowCount(tree.rootIndex()))}


def test_picker_shows_supported_files_and_folders(picker_factory, tmp_path):
    expected = {"alpha.smp", "BETA.SMP", "mixed.SmP", "子文件夹"}
    for name in ("alpha.smp", "BETA.SMP", "mixed.SmP", "unsupported.xls", "notes.txt"):
        (tmp_path / name).touch()
    (tmp_path / "子文件夹").mkdir()
    dialog = picker_factory(tmp_path)

    _wait_until(lambda: _visible_names(dialog) == expected)
    tree = dialog.findChild(QtWidgets.QTreeView, "treeView")
    assert tree.selectionMode() == QtWidgets.QAbstractItemView.ExtendedSelection
    assert [tree.model().headerData(i, QtCore.Qt.Horizontal) for i in range(4)] == ["文件名", "大小", "类型", "修改时间"]
    assert _button(dialog, "选择文件夹") is not None
    assert _button(dialog, "添加") is not None


def test_add_accepts_multiple_files(picker_factory, tmp_path):
    paths = [tmp_path / "样品一.smp", tmp_path / "sample two.SMP"]
    for path in paths:
        path.touch()
    dialog = picker_factory(tmp_path)
    dialog.findChild(QtWidgets.QLineEdit, "fileNameEdit").setText('"样品一.smp" "sample two.SMP"')

    _button(dialog, "添加").click()

    assert dialog.result() == QtWidgets.QDialog.Accepted
    assert {Path(path) for path in dialog.selectedFiles()} == set(paths)
    assert dialog.chosen_directory is None


def test_selecting_folder_preserves_add_label_and_returns_folder(picker_factory, tmp_path):
    folder = tmp_path / "子文件夹"
    folder.mkdir()
    (folder / "child.smp").touch()
    dialog = picker_factory(tmp_path)
    _wait_until(lambda: "子文件夹" in _visible_names(dialog))
    tree = dialog.findChild(QtWidgets.QTreeView, "treeView")
    source = tree.model().sourceModel()
    index = tree.model().mapFromSource(source.index(str(folder)))

    tree.setCurrentIndex(index)
    QtWidgets.QApplication.processEvents()

    assert _button(dialog, "添加").text() == "添加"
    _button(dialog, "选择文件夹").click()
    assert dialog.result() == QtWidgets.QDialog.Accepted
    assert dialog.chosen_directory == folder
    assert str(folder / "child.smp") not in dialog.selectedFiles()


def test_choose_current_folder_without_selecting_files(picker_factory, tmp_path):
    file = tmp_path / "sample.smp"
    file.touch()
    dialog = picker_factory(tmp_path)

    _button(dialog, "选择文件夹").click()

    assert dialog.result() == QtWidgets.QDialog.Accepted
    assert dialog.chosen_directory == tmp_path
    assert str(file) not in dialog.selectedFiles()


def test_cancel_has_no_chosen_directory(picker_factory, tmp_path):
    dialog = picker_factory(tmp_path)
    _button(dialog, "取消").click()
    assert dialog.result() == QtWidgets.QDialog.Rejected
    assert dialog.chosen_directory is None
