import os
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from mercury_app.ui.main_window import MainWindow, QtCore, QtGui, QtWidgets
from pyqtgraph.Qt import QtTest


@pytest.fixture
def copy_window():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = MainWindow()
    window.setAttribute(QtCore.Qt.WA_DontShowOnScreen)
    window.detail_tabs.setCurrentIndex(2)
    table = window.pore_structure_summary_table
    table.setRowCount(3)
    for row in range(3):
        for column in range(table.columnCount()):
            table.setItem(row, column, QtWidgets.QTableWidgetItem(f"R{row}C{column}"))
    window.show()
    QtTest.QTest.qWait(30)
    table.horizontalScrollBar().setValue(table.horizontalScrollBar().maximum())
    app.processEvents()
    yield window, table, app
    window.close()
    app.processEvents()


@pytest.mark.parametrize("reverse", [False, True])
def test_drag_across_frozen_column_copies_hidden_columns(copy_window, reverse):
    window, table, app = copy_window
    frozen = table._frozen_table
    first = frozen.visualRect(table.model().index(0, 0)).center()
    last = table.visualRect(table.model().index(2, table.columnCount() - 1)).center()
    assert table.columnViewportPosition(1) < frozen.width()
    source = table.viewport() if reverse else frozen.viewport()
    start = last if reverse else first
    target_view = frozen.viewport() if reverse else table.viewport()
    target = first if reverse else last
    end = source.mapFromGlobal(target_view.mapToGlobal(target))
    QtTest.QTest.mousePress(source, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, start)
    app.sendEvent(source, QtGui.QMouseEvent(
        QtCore.QEvent.MouseMove, QtCore.QPointF(end),
        QtCore.Qt.NoButton, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier,
    ))
    QtTest.QTest.mouseRelease(source, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, end)
    assert {(i.row(), i.column()) for i in table.selectedIndexes()} == {
        (row, column) for row in range(3) for column in range(table.columnCount())
    }
    window._copy_pore_structure_selection()
    lines = app.clipboard().text().splitlines()
    assert lines[0].split("\t") == [table.horizontalHeaderItem(c).text() for c in range(table.columnCount())]
    assert [line.split("\t") for line in lines[1:]] == [
        [f"R{row}C{column}" for column in range(table.columnCount())] for row in range(3)
    ]


def test_copy_single_metric_adds_names_and_sample_only_copy(copy_window):
    window, table, app = copy_window
    last_column = table.columnCount() - 1
    index = table.model().index(1, last_column)
    QtTest.QTest.mouseClick(table.viewport(), QtCore.Qt.LeftButton, pos=table.visualRect(index).center())
    window._copy_pore_structure_selection()
    assert app.clipboard().text().splitlines() == [
        "样品\t曲折度", f"R1C0\tR1C{last_column}",
    ]
    frozen = table._frozen_table
    QtTest.QTest.mouseClick(frozen.viewport(), QtCore.Qt.LeftButton, pos=frozen.visualRect(table.model().index(1, 0)).center())
    window._copy_pore_structure_selection()
    assert app.clipboard().text().splitlines() == ["样品", "R1C0"]
    QtTest.QTest.mouseClick(
        table.viewport(), QtCore.Qt.LeftButton, QtCore.Qt.ShiftModifier,
        table.visualRect(table.model().index(2, last_column)).center(),
    )
    assert len(table.selectedIndexes()) == 2 * table.columnCount()


def test_sort_header_does_not_select_column(copy_window):
    window, table, app = copy_window
    # Keep the synthetic rows while exercising the real header-click handler.
    sorts = []
    window._sort_samples = lambda key, ascending: sorts.append(ascending)
    header = table.horizontalHeader()
    last_column = table.columnCount() - 1
    position = QtCore.QPoint(header.sectionViewportPosition(last_column) + header.sectionSize(last_column) // 2, header.height() // 2)
    table.selectAll()
    QtTest.QTest.mouseClick(header.viewport(), QtCore.Qt.LeftButton, pos=position)
    assert sorts == [True]
    assert not table.selectedIndexes()
    QtTest.QTest.mouseClick(header.viewport(), QtCore.Qt.LeftButton, pos=position)
    assert sorts == [True, False]
    assert not table.selectedIndexes()
