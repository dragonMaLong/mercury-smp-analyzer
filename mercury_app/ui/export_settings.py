"""BET-style gear settings, adapted to mercury intrusion analysis."""
from __future__ import annotations

import math
from pathlib import Path

from pyqtgraph.Qt import QtCore, QtGui, QtWidgets

from mercury_app.core.export_settings import AppSettings, EXPORT_FORMATS

CONTACT_EMAIL = "kuailelong101@163.com"


def make_gear_icon(size: int = 28, color: str = "#475569") -> QtGui.QIcon:
    pixmap = QtGui.QPixmap(size, size)
    pixmap.fill(QtCore.Qt.transparent)
    painter = QtGui.QPainter(pixmap)
    painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
    c = size / 2.0
    gear = QtGui.QPainterPath()
    gear.setFillRule(QtCore.Qt.OddEvenFill)
    points = []
    for k in range(8):
        base = 2 * math.pi * k / 8
        for angle, radius in ((base - math.pi / 8 + .05, size * .34),
                              (base - math.pi / 8 * .45, size * .46),
                              (base + math.pi / 8 * .45, size * .46),
                              (base + math.pi / 8 - .05, size * .34)):
            points.append(QtCore.QPointF(c + radius * math.cos(angle), c + radius * math.sin(angle)))
    gear.addPolygon(QtGui.QPolygonF(points))
    gear.closeSubpath()
    gear.addEllipse(QtCore.QPointF(c, c), size * .15, size * .15)
    painter.setPen(QtCore.Qt.NoPen)
    painter.setBrush(QtGui.QColor(color))
    painter.drawPath(gear)
    painter.end()
    return QtGui.QIcon(pixmap)


class AppSettingsDialog(QtWidgets.QDialog):
    def __init__(self, parent, current: AppSettings, version: str,
                 import_directory: Path | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("设置")
        self.setMinimumWidth(460)
        self._import_directory = import_directory
        layout = QtWidgets.QVBoxLayout(self)

        export_box = QtWidgets.QGroupBox("导出设置")
        export_form = QtWidgets.QFormLayout(export_box)
        self.format_combo = QtWidgets.QComboBox()
        for key, label in EXPORT_FORMATS.items():
            self.format_combo.addItem(label, key)
        self.format_combo.setCurrentIndex(max(0, self.format_combo.findData(current.export_format)))
        export_form.addRow("默认导出格式", self.format_combo)
        directory_row = QtWidgets.QHBoxLayout()
        self.directory_edit = QtWidgets.QLineEdit(str(current.export_directory))
        self.directory_edit.setReadOnly(True)
        browse = QtWidgets.QPushButton("更改...")
        browse.clicked.connect(self._choose_directory)
        directory_row.addWidget(self.directory_edit, 1)
        directory_row.addWidget(browse)
        export_form.addRow("默认导出文件夹", directory_row)
        layout.addWidget(export_box)

        display_box = QtWidgets.QGroupBox("结果显示")
        display_form = QtWidgets.QFormLayout(display_box)
        self.orientation_combo = QtWidgets.QComboBox()
        self.orientation_combo.addItem("样品按列", True)
        self.orientation_combo.addItem("样品按行", False)
        self.orientation_combo.setCurrentIndex(0 if current.samples_as_columns else 1)
        self.orientation_combo.setToolTip("控制孔结构结果表和 Excel 分析表的行列方向。")
        display_form.addRow("结果表方向", self.orientation_combo)
        layout.addWidget(display_box)

        about_box = QtWidgets.QGroupBox("关于与反馈")
        about_layout = QtWidgets.QVBoxLayout(about_box)
        about_layout.addWidget(QtWidgets.QLabel(f"当前版本：v{version}"))
        contact = QtWidgets.QLabel(f'导入失败或发现问题，请联系：<br><a href="mailto:{CONTACT_EMAIL}">{CONTACT_EMAIL}</a>')
        contact.setTextFormat(QtCore.Qt.RichText)
        contact.setOpenExternalLinks(True)
        contact.setTextInteractionFlags(QtCore.Qt.TextBrowserInteraction)
        contact.setWordWrap(True)
        about_layout.addWidget(contact)
        about_buttons = QtWidgets.QHBoxLayout()
        supported = QtWidgets.QPushButton("查看支持的设备型号")
        supported.clicked.connect(self._show_supported_instruments)
        about_buttons.addWidget(supported)
        copy = QtWidgets.QPushButton("复制邮箱")
        copy.clicked.connect(lambda: QtWidgets.QApplication.clipboard().setText(CONTACT_EMAIL))
        about_buttons.addWidget(copy)
        open_import = QtWidgets.QPushButton("打开上次导入的文件夹")
        open_import.setEnabled(bool(import_directory and Path(import_directory).is_dir()))
        open_import.clicked.connect(self._open_import_directory)
        about_buttons.addWidget(open_import)
        about_buttons.addStretch(1)
        about_layout.addLayout(about_buttons)
        layout.addWidget(about_box)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        buttons.button(QtWidgets.QDialogButtonBox.Ok).setText("确定")
        buttons.button(QtWidgets.QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _choose_directory(self) -> None:
        chosen = QtWidgets.QFileDialog.getExistingDirectory(self, "选择默认导出文件夹", self.directory_edit.text())
        if chosen:
            self.directory_edit.setText(str(Path(chosen)))

    def _show_supported_instruments(self) -> None:
        QtWidgets.QMessageBox.information(self, "支持的设备型号",
            "Micromeritics AutoPore / MicroActive\n文件格式：压汞 SMP (.SMP)\n\n"
            "支持读取入汞/退汞数据、Washburn 参数与样品信息，并计算孔径分布、Mayer-Stowe 和孔结构结果。\n\n"
            f"不同软件版本的文件可能存在差异；无法导入时可联系 {CONTACT_EMAIL}。")

    def _open_import_directory(self) -> None:
        if self._import_directory:
            QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(self._import_directory)))

    def values(self) -> AppSettings:
        return AppSettings(str(self.format_combo.currentData()), Path(self.directory_edit.text()),
                           bool(self.orientation_combo.currentData()))
