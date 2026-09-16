import os
from pathlib import Path
import sys

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SAMPLE = ROOT.parent / "1-QC3.0.SMP"
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from mercury_app.ui.main_window import MainWindow, QtCore, QtWidgets
from pyqtgraph.Qt import QtTest


@pytest.fixture(scope="module")
def application():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield app
    app.processEvents()


@pytest.mark.skipif(not SAMPLE.exists(), reason="Local SMP reference file is unavailable")
def test_pore_pressure_labels_drag_edit_cancel_and_redraw(application):
    app = application
    window = MainWindow()
    window.setAttribute(QtCore.Qt.WA_DontShowOnScreen)
    try:
        window.load_file(SAMPLE)
        window.analysis_tabs.setCurrentIndex(2)
        window.resize(1800, 1100)
        window.show()
        QtTest.QTest.qWait(30)
        controls = window.pore_pressure_endpoints
        plot = window.pore_structure_pressure_plot
        original_pore_size = window._current_pressure_region()
        original_mayer = window._current_mayer_pressure_region()
        assert [label.toPlainText() for label in controls.labels] == ["400", "10,000"]

        # Both numbers and the upper graph update before releasing the drag.
        region = controls.region
        labels = list(controls.labels)
        lower_curves = list(plot.listDataItems())
        region.lines[0].setValue(float(np.log10(800)))
        assert controls.labels[0].toPlainText() == "800"

        def assert_percent_range(lo, hi):
            analysis = window.pore_structure_results[0]
            assert analysis.pressure[[0, -1]] == pytest.approx([lo, hi])
            assert analysis.cumulative_percent[[0, -1]] == pytest.approx([0., 100.])
            controller = window.pore_structure_percent_plot._sample_curve_interaction_controller
            assert controller.entries
            for entry in controller.entries:
                assert entry["x"][[0, -1]] == pytest.approx([lo, hi])
                assert entry["y"][[0, -1]] == pytest.approx([0., 100.])

        assert controls.labels[0].isVisible()
        assert controls.region is region
        QtTest.QTest.qWait(60)
        assert_percent_range(800., 10000.)
        # Further moves of either edge still work without any finished signal.
        for edge, pressure, expected in (
            (0, 1200., [1200., 10000.]),
            (1, 9000., [1200., 9000.]),
            (0, 800., [800., 9000.]),
            (1, 10000., [800., 10000.]),
        ):
            region.lines[edge].setValue(float(np.log10(pressure)))
            QtTest.QTest.qWait(60)
            assert_percent_range(*expected)
            assert controls.region is region
            assert controls.labels == labels
            assert plot.listDataItems() == lower_curves
        region.sigRegionChangeFinished.emit(region)
        QtTest.QTest.qWait(60)
        assert controls.region is region
        assert controls.labels[0].toPlainText() == "800"
        assert_percent_range(800., 10000.)

        def click_label(index):
            controls.show_labels()
            app.processEvents()
            point = plot.mapFromScene(controls.labels[index].sceneBoundingRect().center())
            QtTest.QTest.mouseClick(plot.viewport(), QtCore.Qt.LeftButton, pos=point)
            assert not controls.editor.isHidden()
            assert controls.editing_index == index

        for index, text in ((0, "3100"), (1, "8100")):
            click_label(index)
            QtTest.QTest.keyClicks(controls.editor, text)
            QtTest.QTest.keyClick(controls.editor, QtCore.Qt.Key_Return)
            QtTest.QTest.qWait(60)
        assert window._current_pore_structure_pressure_region() == pytest.approx([3100, 8100])
        assert [label.toPlainText() for label in controls.labels] == ["3,100", "8,100"]
        assert_percent_range(3100., 8100.)
        assert window._current_pressure_region() == pytest.approx(original_pore_size)
        assert window._current_mayer_pressure_region() == pytest.approx(original_mayer)

        click_label(0)
        QtTest.QTest.keyClicks(controls.editor, "nan")
        QtTest.QTest.keyClick(controls.editor, QtCore.Qt.Key_Return)
        app.processEvents()
        assert not controls.editor.isHidden()
        assert window._current_pore_structure_pressure_region() == pytest.approx([3100, 8100])
        QtTest.QTest.keyClick(controls.editor, QtCore.Qt.Key_Escape)
        assert controls.editor.isHidden()
        window.update_pore_structure()
        assert controls.region is window.pore_structure_pressure_region
        assert len(controls.labels) == 2
        assert [label.toPlainText() for label in controls.labels] == ["3,100", "8,100"]
    finally:
        window.close()
        app.processEvents()
