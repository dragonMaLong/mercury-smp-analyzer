from __future__ import annotations

import os
from pathlib import Path
import sys

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = PROJECT_ROOT.parent
sys.path.insert(0, str(PROJECT_ROOT))


@pytest.mark.parametrize("loaded", [False, True])
def test_header_boundary_and_parameter_column_dragging(loaded: bool) -> None:
    if loaded and not (DATA_ROOT / "1-QC3.0.SMP").exists():
        pytest.skip("Local SMP reference file is unavailable.")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from mercury_app.ui.main_window import (
        ANGLE_COLUMN, TEST_TIME_COLUMN, MainWindow, QtCore, QtGui, QtWidgets,
    )
    from pyqtgraph.Qt import QtTest

    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = MainWindow()
    try:
        window.resize(2200, 1000)
        if loaded:
            window.load_file(DATA_ROOT / "1-QC3.0.SMP")
        window.show()
        application.processEvents()
        table = window.sample_list
        frozen = table._frozen_table
        header_image = frozen.horizontalHeader().viewport().grab().toImage()
        assert header_image.pixelColor(header_image.width() - 1, header_image.height() // 2).name() == "#d1d5db"
        body_image = frozen.viewport().grab().toImage()
        assert body_image.pixelColor(body_image.width() - 1, body_image.height() - 5).name() == "#ffffff"

        header = table.horizontalHeader()
        for column in (TEST_TIME_COLUMN, ANGLE_COLUMN):
            before = [table.columnWidth(i) for i in range(table.columnCount())]
            start = QtCore.QPoint(
                header.sectionViewportPosition(column) + header.sectionSize(column) - 1,
                header.height() // 2,
            )
            end = start + QtCore.QPoint(25, 0)
            QtTest.QTest.mousePress(header.viewport(), QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, start)
            move = QtGui.QMouseEvent(
                QtCore.QEvent.MouseMove, QtCore.QPointF(end),
                QtCore.Qt.NoButton, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier,
            )
            application.sendEvent(header.viewport(), move)
            QtTest.QTest.mouseRelease(header.viewport(), QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, end)
            application.processEvents()
            expected = before[:]
            expected[column] += 25
            assert [table.columnWidth(i) for i in range(table.columnCount())] == expected
    finally:
        window.close()
        application.processEvents()


@pytest.mark.skipif(
    not (DATA_ROOT / "1-QC3.0.SMP").exists() or not (DATA_ROOT / "2-BTR-8.SMP").exists(),
    reason="Local SMP reference files are unavailable.",
)
def test_parameter_spin_editor_commits_on_enter_and_applies_to_all() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from pyqtgraph.Qt import QtCore, QtTest, QtWidgets
    from mercury_app.ui.main_window import ANGLE_COLUMN, TENSION_COLUMN, MainWindow

    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = MainWindow()
    try:
        window.load_files([DATA_ROOT / "1-QC3.0.SMP", DATA_ROOT / "2-BTR-8.SMP"])
        window.show()
        application.processEvents()

        window.on_sample_item_clicked(window.sample_list.item(0, ANGLE_COLUMN))
        QtTest.QTest.qWait(100)
        editor = application.focusWidget()
        assert isinstance(editor, QtWidgets.QDoubleSpinBox)
        assert editor.decimals() == 2
        assert editor.singleStep() == pytest.approx(1.0)
        assert editor.buttonSymbols() == QtWidgets.QAbstractSpinBox.UpDownArrows
        assert editor.suffix() == ""
        assert editor.lineEdit().selectedText() == ""
        assert window.sample_list.horizontalHeader().stretchLastSection()
        assert window.sample_list.columnWidth(ANGLE_COLUMN) == window.sample_list.columnWidth(TENSION_COLUMN)
        frozen_table = window.sample_list._frozen_table
        header_center = window.select_all_check.mapTo(
            window.sample_list,
            window.select_all_check.rect().center(),
        ).x()
        row_center = frozen_table.viewport().mapTo(
            window.sample_list,
            frozen_table.visualRect(frozen_table.model().index(0, 0)).center(),
        ).x()
        assert abs(header_center - row_center) <= 1
        assert window.sample_list.item(0, ANGLE_COLUMN).text() == "130"
        assert not hasattr(window, "angle_info_button")
        assert not hasattr(window, "surface_info_button")
        shown_info = []
        window.show_header_info = lambda title, text, section=None: shown_info.append((title, text, section))
        window.on_sample_header_clicked(ANGLE_COLUMN)
        window.on_sample_header_clicked(TENSION_COLUMN)
        assert shown_info[0][0] == "进汞接触角/°"
        assert shown_info[0][2] == ANGLE_COLUMN
        assert shown_info[1][0] == "表面张力/dynes/cm"
        assert shown_info[1][2] == TENSION_COLUMN

        editor.setValue(140.0)
        QtTest.QTest.keyClick(editor, QtCore.Qt.Key_Return)
        QtTest.QTest.qWait(50)
        assert window.results[0].metadata["adv_contact_angle_deg"] == pytest.approx(140.0)

        original_tensions = [result.metadata["surface_tension_dynes_cm"] for result in window.results]
        window.apply_sample_parameter_to_all(0, ANGLE_COLUMN)
        application.processEvents()
        assert all(result.metadata["adv_contact_angle_deg"] == pytest.approx(140.0) for result in window.results)
        assert [result.metadata["surface_tension_dynes_cm"] for result in window.results] == original_tensions
    finally:
        window.close()
        application.processEvents()


@pytest.mark.skipif(not (DATA_ROOT / "1-QC3.0.SMP").exists(), reason="Local SMP reference file is unavailable.")
def test_import_into_open_window_aligns_circles_and_fills_right_edge() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from mercury_app.ui.main_window import MainWindow, QtCore, QtWidgets
    from pyqtgraph.Qt import QtTest

    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = MainWindow()
    window.setAttribute(QtCore.Qt.WA_DontShowOnScreen)
    try:
        window.resize(2500, 1000)
        window.show()
        QtTest.QTest.qWait(30)
        window.load_files([DATA_ROOT / "1-QC3.0.SMP"] * 7)
        QtTest.QTest.qWait(30)
        table = window.sample_list
        frozen = table._frozen_table

        def check_alignment():
            assert table.columnWidth(0) == frozen.columnWidth(0)
            assert frozen.horizontalHeader().offset() == 0
            header_center = window.select_all_check.mapTo(table, window.select_all_check.rect().center()).x()
            row_center = frozen.viewport().mapTo(table, frozen.visualRect(frozen.model().index(0, 0)).center()).x()
            assert abs(header_center - row_center) <= 1
            header = table.horizontalHeader()
            assert header.sectionViewportPosition(4) + header.sectionSize(4) == table.viewport().width()
            assert table.columnWidth(3) == table.columnWidth(4)

        check_alignment()
        window.resize(2700, 1000)
        QtTest.QTest.qWait(30)
        check_alignment()
    finally:
        window.close()
        application.processEvents()
