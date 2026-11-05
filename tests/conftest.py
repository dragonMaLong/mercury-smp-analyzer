"""Keep Qt alive and keep every test away from real user settings/services."""
import os
import gc
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
def isolate_ui_updates(request, monkeypatch, tmp_path, qt_runtime):
    from mercury_app.ui.main_window import MainWindow, QtCore
    from PyQt5 import sip
    original_settings = QtCore.QSettings
    original_init = MainWindow.__init__
    test_windows = []
    settings_path = tmp_path / 'isolated-ui.ini'
    legacy_rows = request.node.path.name.startswith(('test_pore_', 'test_sample_', 'test_mayer_stowe'))

    class IsolatedSettings(original_settings):
        def __init__(self, *args, **kwargs):
            # Explicit test-owned INI files remain usable; organization/name
            # constructors always redirect away from the registry/user profile.
            if len(args) >= 2 and isinstance(args[1], original_settings.Format):
                super().__init__(*args, **kwargs)
            else:
                super().__init__(str(settings_path), original_settings.IniFormat)
                if legacy_rows and not self.contains('display/samples_as_columns'):
                    self.setValue('display/samples_as_columns', False)

    monkeypatch.setattr(QtCore, 'QSettings', IsolatedSettings)
    def tracked_init(window, *args, **kwargs):
        # Keep the Python owners alive until deterministic Qt teardown. Merely
        # closing a window does not destroy its graphics scene or queued timers.
        original_init(window, *args, **kwargs)
        test_windows.append(window)

    monkeypatch.setattr(MainWindow, '__init__', tracked_init)
    monkeypatch.setattr(MainWindow, '_auto_check_for_updates', lambda self: None)
    monkeypatch.setattr(MainWindow, '_initialize_telemetry', lambda self: None, raising=False)
    monkeypatch.setenv('MERCURY_TELEMETRY_URL', '')
    yield
    # A preceding test must not leave a closed pyqtgraph window to be collected
    # while the next test processes paint/timer events. Destroy the full Qt tree
    # now, while its Python wrappers and per-test configuration are still alive.
    for window in test_windows:
        if sip.isdeleted(window):
            continue
        window.close()
        for timer in window.findChildren(QtCore.QTimer):
            timer.stop()
        window.deleteLater()
    QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)
    test_windows.clear()
    gc.collect()
    QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)
