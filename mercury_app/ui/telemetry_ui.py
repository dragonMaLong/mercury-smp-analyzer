"""Optional usage statistics hooks shared by the main window and settings."""
from __future__ import annotations

import re
import time
from pathlib import Path

from pyqtgraph.Qt import QtCore, QtWidgets

from mercury_app.core import load_smp
from mercury_app.telemetry import TelemetryClient, configured_endpoint
from mercury_app.ui.telemetry_settings import TelemetrySettingsDialog, startup_environment
from mercury_app.ui.usage_timer import UsageTimer
from mercury_app.version import __version__


def telemetry_instrument(result) -> str:
    """Map parsed metadata to a fixed category; never return its original text."""
    name = str(getattr(result, "metadata", {}).get("instrument_name", "")).casefold()
    if "autopore" in name or "auto pore" in name:
        if re.search(r"\biv\b|\b95\d{2}\b", name):
            return "autopore_iv"
        if re.search(r"\bv\b|\b96\d{2}\b", name):
            return "autopore_v"
        return "autopore_other"
    if "micromeritics" in name:
        return "micromeritics_other"
    return "other"


class TelemetryUiMixin:
    def _init_telemetry(self) -> None:
        """Call once after the window's tabs and status bar have been built."""
        if hasattr(self, "_telemetry_initialized"):
            return
        self._telemetry = None
        self._telemetry_initialized = False
        self._telemetry_closed = False
        self._telemetry_startup_sent = False
        self._usage_timer = None
        self.privacy_button = QtWidgets.QToolButton(self)
        self.privacy_button.setText("隐私设置")
        self.privacy_button.setAutoRaise(True)
        self.privacy_button.setToolTip("管理可选的压汞软件使用统计")
        self.privacy_button.clicked.connect(self.show_telemetry_settings)
        self.statusBar().addPermanentWidget(self.privacy_button)
        # Keep the entry point available for a future release, but hide it for now.
        self.privacy_button.hide()
        self.analysis_tabs.currentChanged.connect(lambda index: self._record_feature_view(index, "plot"))
        self.detail_tabs.currentChanged.connect(lambda index: self._record_feature_view(index, "results"))
        QtCore.QTimer.singleShot(800, self._initialize_telemetry)

    def _initialize_telemetry(self) -> None:
        if getattr(self, "_telemetry_closed", False) or getattr(self, "_telemetry_initialized", False):
            return
        self._telemetry_initialized = True
        try:
            storage = QtCore.QStandardPaths.writableLocation(QtCore.QStandardPaths.GenericDataLocation)
            if not storage:
                return
            # Explicit application directory keeps IDs/preferences separate from BET.
            directory = Path(storage) / "DragonScience" / "MercurySMP" / "telemetry"
            self._telemetry = TelemetryClient(directory, __version__, configured_endpoint())
            self._record_telemetry_startup()
        except Exception:
            # Optional telemetry can never prevent analysis or window startup.
            return

    def show_telemetry_settings(self) -> None:
        client = getattr(self, "_telemetry", None)
        if client is not None and client.storage_busy:
            client.close()
            self._telemetry = None
            self._telemetry_initialized = False
        self._initialize_telemetry()
        client = getattr(self, "_telemetry", None)
        if client is None:
            QtWidgets.QMessageBox.information(self, "隐私设置", "无法访问统计设置存储，使用统计保持关闭。")
            return
        if client.storage_busy:
            QtWidgets.QMessageBox.information(
                self, "隐私设置", "使用统计由另一个压汞软件窗口管理，本窗口不会记录或发送统计。\n"
                "请关闭其他压汞软件窗口后，再打开这里管理统计设置。",
            )
            return
        dialog = TelemetrySettingsDialog(client, self)
        if dialog.exec_() == QtWidgets.QDialog.Accepted:
            self._apply_telemetry_consent(dialog.enable_check.isChecked())

    def _apply_telemetry_consent(self, enabled: bool) -> None:
        client = getattr(self, "_telemetry", None)
        if client is None:
            return
        if not client.set_enabled(enabled):
            QtWidgets.QMessageBox.warning(self, "隐私设置", "无法保存统计设置，统计已保持关闭。")
        if client.enabled:
            self._record_telemetry_startup()
        else:
            self._telemetry_startup_sent = False
            if self._usage_timer is not None:
                self._usage_timer.finish()
                self._usage_timer.deleteLater()
                self._usage_timer = None

    def _record_telemetry(self, name: str, **data) -> None:
        client = getattr(self, "_telemetry", None)
        if client is not None:
            try:
                client.record(name, **data)
            except Exception:
                pass

    def _record_telemetry_startup(self) -> None:
        client = getattr(self, "_telemetry", None)
        if client is None or not client.enabled or self._telemetry_startup_sent:
            return
        self._telemetry_startup_sent = True
        self._usage_timer = UsageTimer(self)
        try:
            self._record_telemetry("startup", **startup_environment(self.screen()))
        except Exception:
            pass

    def _record_feature_view(self, index: int, surface: str) -> None:
        features = (
            ("pore_distribution", "mayer_stowe", "pore_structure") if surface == "plot"
            else ("sample_info", "selected_pore_volume", "pore_structure")
        )
        if 0 <= index < len(features):
            self._record_telemetry("feature_view", feature=features[index], surface=surface)

    def _load_file_with_telemetry(self, path, loader=None):
        started = time.monotonic()
        error_code = "none"
        result = None
        extension = "smp" if Path(path).suffix.lower() == ".smp" else "other"
        try:
            result = (loader or load_smp)(path)
            if result.data_point_count <= 0:
                error_code = "no_points"
            return result
        except OSError:
            error_code = "io_error"
            raise
        except ValueError:
            error_code = "parse_error"
            raise
        except Exception:
            error_code = "unexpected_error"
            raise
        finally:
            fields = {"instrument": telemetry_instrument(result)} if result is not None else {}
            self._record_telemetry(
                "import_file", extension=extension, parser="micromeritics" if extension == "smp" else "unknown",
                status="success" if error_code == "none" else "failure", error_code=error_code,
                duration_ms=min(86400000, max(0, int((time.monotonic() - started) * 1000))), **fields,
            )

    def _close_telemetry(self) -> None:
        if getattr(self, "_telemetry_closed", False):
            return
        usage_timer = getattr(self, "_usage_timer", None)
        if usage_timer is not None:
            open_s, active_s = usage_timer.finish()
            self._record_telemetry("session_end", duration_s=open_s, active_s=active_s)
        self._telemetry_closed = True
        client = getattr(self, "_telemetry", None)
        if client is not None:
            client.close()
