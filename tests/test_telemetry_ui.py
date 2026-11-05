"""Exercise the real window hooks with temporary consent and mocked transport."""
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from mercury_app import telemetry
from mercury_app.ui import telemetry_ui
from mercury_app.ui.main_window import MainWindow, QtCore
from mercury_app.ui.telemetry_settings import TelemetryDetailsDialog, TelemetrySettingsDialog, startup_environment
from mercury_app.ui.telemetry_ui import TelemetryUiMixin, telemetry_instrument
from mercury_app.ui.usage_timer import UsageTimer
from mercury_telemetry.schema import APP_ID, validate_event, validate_batch


def event(name, **data):
    return {"event_id": "6f1c1a4e-8d4b-4a8e-9b2a-3c2d1e0f9a7b", "occurred_at": "2026-11-05T08:00:00Z",
            "name": name, "data": data}


@pytest.fixture
def telemetry_window(tmp_path, monkeypatch):
    monkeypatch.setattr(telemetry_ui, "configured_endpoint", lambda: "https://telemetry.example.test/api/v1/events")
    monkeypatch.setattr(QtCore.QStandardPaths, "writableLocation", lambda _kind: str(tmp_path))
    monkeypatch.setattr(telemetry.TelemetryClient, "_start_worker_locked", lambda self: None)
    network = Mock(side_effect=AssertionError("Tests must not use network"))
    monkeypatch.setattr(telemetry, "_send_batch", network)
    window = MainWindow()
    # Global fixture disables the scheduled hook; call the original explicitly.
    TelemetryUiMixin._initialize_telemetry(window)
    yield window
    window.close()
    window.deleteLater()
    network.assert_not_called()


def test_window_defaults_on_and_records_tabs_import_export_close(telemetry_window):
    window = telemetry_window
    client = window._telemetry
    assert client is not None and client.available and client.enabled
    assert client.pending_count == 1
    assert window._usage_timer is not None
    assert window.privacy_button.isHidden()
    window._apply_telemetry_consent(True)
    assert client.enabled
    assert [entry["event"]["name"] for entry in client._queue] == ["startup"]
    window.analysis_tabs.setCurrentIndex(1)
    window.detail_tabs.setCurrentIndex(2)
    sample = SimpleNamespace(data_point_count=3, metadata={"instrument_name": "AutoPore V 9620"})
    assert window._load_file_with_telemetry("PRIVATE-SAMPLE.smp", lambda path: sample) is sample
    window._record_telemetry("export", format="pdf", status="success", duration_ms=12, error_code="none")
    window._usage_timer.finish = lambda: (125, 40)
    window.close()
    window.close()
    events = [entry["event"] for entry in client._queue]
    assert [record["name"] for record in events] == ["startup", "feature_view", "feature_view", "import_file", "export", "session_end"]
    assert events[1]["data"]["feature"] == "mayer_stowe"
    assert events[2]["data"]["feature"] == "pore_structure"
    assert events[3]["data"]["instrument"] == "autopore_v"
    assert events[-1]["data"] == {"duration_s": 125, "active_s": 40}
    for record in events:
        validate_event(record)
    assert "PRIVATE" not in json.dumps(events)


def test_disabling_clears_queue_timer_and_install_identity(telemetry_window):
    window = telemetry_window
    window._apply_telemetry_consent(True)
    first_id = window._telemetry._install_id
    window._apply_telemetry_consent(False)
    assert not window._telemetry.enabled
    assert window._telemetry.pending_count == 0
    assert window._telemetry._install_id is None
    assert window._usage_timer is None
    window._apply_telemetry_consent(True)
    assert window._telemetry._install_id != first_id
    assert window._telemetry.pending_count == 1


def test_parse_failure_uses_fixed_error_code_without_exception_text(telemetry_window):
    window = telemetry_window
    window._apply_telemetry_consent(True)
    def broken(_path):
        raise ValueError("PRIVATE FILE CONTENTS")
    with pytest.raises(ValueError):
        window._load_file_with_telemetry("PRIVATE-PATH.smp", broken)
    record = window._telemetry._queue[-1]["event"]
    assert record["data"]["error_code"] == "parse_error"
    assert record["data"]["status"] == "failure"
    assert "instrument" not in record["data"]
    assert "PRIVATE" not in json.dumps(record)


def test_settings_exposes_consent_and_service_address(telemetry_window):
    client = telemetry_window._telemetry
    dialog = TelemetrySettingsDialog(client, telemetry_window)
    assert dialog.enable_check.isChecked()
    assert dialog.enable_check.isEnabled()
    details = TelemetryDetailsDialog(client, dialog)
    text = "\n".join(label.text() for label in details.findChildren(telemetry_ui.QtWidgets.QLabel))
    assert client.endpoint in text
    assert "默认勾选" in text and "文件名或路径" in text
    dialog.deleteLater()


def test_settings_keeps_saved_opt_out_after_restart(tmp_path, qt_runtime, monkeypatch):
    monkeypatch.setattr(telemetry.TelemetryClient, "_start_worker_locked", lambda self: None)
    endpoint = "https://telemetry.example.test/api/v1/events"
    first = telemetry.TelemetryClient(tmp_path, "1.1.2", endpoint)
    try:
        assert first.set_enabled(False)
    finally:
        first.close()
    restarted = telemetry.TelemetryClient(tmp_path, "1.1.2", endpoint)
    try:
        dialog = TelemetrySettingsDialog(restarted)
        assert not dialog.enable_check.isChecked()
        assert not restarted.enabled
        dialog.deleteLater()
    finally:
        restarted.close()


def test_settings_shows_default_checked_without_configured_service(tmp_path, qt_runtime):
    client = telemetry.TelemetryClient(tmp_path, "1.1.2", "")
    try:
        dialog = TelemetrySettingsDialog(client)
        assert dialog.enable_check.isChecked()
        assert dialog.enable_check.isEnabled()
        assert not client.enabled
        assert not client.record("feature_view", feature="pore_structure", surface="results")
        assert client._worker is None
        dialog.enable_check.setChecked(False)
        assert client.set_enabled(dialog.enable_check.isChecked())
        assert not client.preference_enabled
        dialog.deleteLater()
    finally:
        client.close()


def test_application_protocol_rejects_bet_or_unidentified_batches():
    batch = {"schema_version": 1, "app_id": APP_ID,
             "install_id": "0c9650d2-a02f-437b-96a1-5bfbbed88180",
             "session_id": "1c9650d2-a02f-437b-96a1-5bfbbed88180", "app_version": "1.1.2",
             "events": [event("export", format="pdf", status="success", duration_ms=1, error_code="none")]}
    assert validate_batch(batch)["app_id"] == "mercury_smp"
    with pytest.raises(ValueError):
        validate_batch({**batch, "app_id": "bet"})
    batch.pop("app_id")
    with pytest.raises(ValueError):
        validate_batch(batch)


def test_instrument_and_startup_environment_are_bounded():
    for name, expected in (("AutoPore IV 9500", "autopore_iv"), ("AutoPore V 9620", "autopore_v"),
                           ("AutoPore", "autopore_other"), ("Private laboratory", "other")):
        assert telemetry_instrument(SimpleNamespace(metadata={"instrument_name": name})) == expected
    validate_event(event("startup", **startup_environment(None)))


def test_usage_timer_excludes_background_idle_and_sleep(qt_runtime):
    clock = SimpleNamespace(now=1000.0)
    timer = UsageTimer(clock=lambda: clock.now)
    for _ in range(12):
        clock.now += 5
        timer.tick(foreground=True)
    clock.now += 10
    timer.tick(foreground=False)
    clock.now += 3600
    timer.tick(foreground=True)
    assert timer._active == 60
    timer._foreground = lambda: False
    open_s, active_s = timer.finish()
    assert (open_s, active_s) == (3670, 60)
    assert timer.finish() == (open_s, active_s)
    timer.deleteLater()
