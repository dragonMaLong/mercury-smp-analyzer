"""BET-style file/folder selection for platforms without the Windows picker."""

from pathlib import Path

from pyqtgraph.Qt import QtCore, QtWidgets


class _ImportFilePickerModel(QtCore.QIdentityProxyModel):
    def headerData(self, section, orientation, role=QtCore.Qt.DisplayRole):
        labels = ("文件名", "大小", "类型", "修改时间")
        if orientation == QtCore.Qt.Horizontal and role == QtCore.Qt.DisplayRole and 0 <= section < len(labels):
            return labels[section]
        return super().headerData(section, orientation, role)


class ImportFilePicker(QtWidgets.QFileDialog):
    """Select files to add, or return a folder for the import list to browse."""

    def __init__(self, parent=None, initial_dir: Path | str | None = None, suffixes=(".smp",)) -> None:
        super().__init__(parent)
        self.setWindowFlag(QtCore.Qt.WindowContextHelpButtonHint, False)
        self.setOption(QtWidgets.QFileDialog.DontUseNativeDialog, True)
        self.setWindowTitle("选择数据文件夹")
        self.setFileMode(QtWidgets.QFileDialog.ExistingFiles)
        self.setAcceptMode(QtWidgets.QFileDialog.AcceptOpen)
        self.setViewMode(QtWidgets.QFileDialog.Detail)
        self.setProxyModel(_ImportFilePickerModel(self))
        # Omitting QDir.CaseSensitive also accepts .SMP and mixed-case suffixes
        # on platforms with case-sensitive file systems.
        self.setFilter(QtCore.QDir.AllDirs | QtCore.QDir.Files | QtCore.QDir.NoDotAndDotDot)
        self.setDirectory(str(initial_dir or Path.cwd()))
        patterns = " ".join(f"*{suffix}" for suffix in suffixes)
        self.setNameFilter(f"支持的数据文件 ({patterns})")
        self.setLabelText(QtWidgets.QFileDialog.LookIn, "当前文件夹：")
        self.setLabelText(QtWidgets.QFileDialog.FileName, "文件名：")
        self.setLabelText(QtWidgets.QFileDialog.FileType, "文件类型：")
        self.setLabelText(QtWidgets.QFileDialog.Accept, "添加")
        self.setLabelText(QtWidgets.QFileDialog.Reject, "取消")
        self.chosen_directory: Path | None = None

        for name, tooltip in (
            ("backButton", "后退"), ("forwardButton", "前进"),
            ("toParentButton", "上一级文件夹"), ("newFolderButton", "新建文件夹"),
            ("listModeButton", "列表视图"), ("detailModeButton", "详细信息视图"),
        ):
            self.findChild(QtWidgets.QToolButton, name).setToolTip(tooltip)

        button_box = self.findChild(QtWidgets.QDialogButtonBox)
        button_box.setOrientation(QtCore.Qt.Horizontal)
        self._add_button = button_box.button(QtWidgets.QDialogButtonBox.Open)
        # Qt changes this label to Open when a directory is highlighted.
        self.currentChanged.connect(self._restore_add_button_label)
        self.directoryEntered.connect(self._restore_add_button_label)
        self.findChild(QtWidgets.QLineEdit, "fileNameEdit").textChanged.connect(self._restore_add_button_label)
        for name in ("listView", "treeView"):
            self.findChild(QtWidgets.QAbstractItemView, name).selectionModel().selectionChanged.connect(self._restore_add_button_label)
        folder_button = button_box.addButton("选择文件夹", QtWidgets.QDialogButtonBox.ActionRole)
        folder_button.setAutoDefault(False)
        folder_button.setToolTip("只选择文件夹，返回后在左侧排序、筛选文件")
        folder_button.clicked.connect(self._choose_directory)

        hint = QtWidgets.QLabel("可用 Ctrl / Shift 多选文件后点击“添加”；也可点击“选择文件夹”，返回后再排序、筛选文件。")
        hint.setWordWrap(True)
        layout = self.layout()
        layout.removeWidget(button_box)
        for name in ("fileNameEdit", "fileTypeCombo"):
            widget = self.findChild(QtWidgets.QWidget, name)
            row, column, row_span, _ = layout.getItemPosition(layout.indexOf(widget))
            layout.removeWidget(widget)
            layout.addWidget(widget, row, column, row_span, 2)
        row = layout.rowCount()
        layout.addWidget(hint, row, 0, 1, layout.columnCount())
        layout.addWidget(button_box, row + 1, 0, 1, layout.columnCount())
        self.resize(900, 560)

    def _restore_add_button_label(self, *_args) -> None:
        self._add_button.setText("添加")

    def _choose_directory(self) -> None:
        selected = self.selectedFiles()
        directory = Path(self.directory().absolutePath())
        if len(selected) == 1 and Path(selected[0]).is_dir():
            directory = Path(selected[0])
        if not directory.is_dir():
            QtWidgets.QMessageBox.warning(self, "文件夹不存在", str(directory))
            return
        self.chosen_directory = directory
        # The folder action intentionally skips QFileDialog's file validation.
        QtWidgets.QDialog.accept(self)
