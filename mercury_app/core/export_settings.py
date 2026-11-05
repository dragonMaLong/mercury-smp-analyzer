"""Persistent export/display preferences shared by the toolbar and result table."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

EXPORT_FORMATS = {"xlsx": "Excel 工作簿 (.xlsx)", "pdf": "PDF 报告 (.pdf)"}


@dataclass
class AppSettings:
    export_format: str
    export_directory: Path
    samples_as_columns: bool = True

    @classmethod
    def from_settings(cls, settings, default_directory: Path | None = None) -> "AppSettings":
        fallback = Path(default_directory or Path.home())
        directory = Path(str(settings.value("export_directory", str(fallback))))
        if not directory.is_dir():
            directory = fallback
        file_format = str(settings.value("export/default_format", "xlsx"))
        if file_format not in EXPORT_FORMATS:
            file_format = "xlsx"
        orientation = settings.value("display/samples_as_columns", True)
        if isinstance(orientation, str):
            orientation = orientation.lower() not in {"false", "0", "no", ""}
        return cls(file_format, directory, bool(orientation))

    def save(self, settings) -> None:
        settings.setValue("export/default_format", self.export_format if self.export_format in EXPORT_FORMATS else "xlsx")
        settings.setValue("export_directory", str(self.export_directory))
        settings.setValue("display/samples_as_columns", bool(self.samples_as_columns))
