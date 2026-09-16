"""Keep Qt alive across UI tests and isolate them from live update requests."""
import os
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('PYQTGRAPH_QT_LIB', 'PyQt5')

_test_application = None


@pytest.fixture(scope='session')
def qt_runtime():
    global _test_application
    from mercury_app.ui.main_window import QtWidgets
    _test_application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield _test_application
    _test_application.clipboard().clear()
    _test_application.processEvents()


@pytest.fixture(autouse=True)
def isolate_ui_updates(request, monkeypatch):
    if request.node.path.name.startswith(('test_pore_', 'test_sample_', 'test_mayer_stowe')):
        request.getfixturevalue('qt_runtime')
        from mercury_app.ui.main_window import MainWindow
        monkeypatch.setattr(MainWindow, '_auto_check_for_updates', lambda self: None)
