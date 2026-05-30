from __future__ import annotations

import re
from pathlib import Path
from typing import Sequence

from .metrics import summary_metrics
from .models import MercuryResult


def export_results_xlsx(results: Sequence[MercuryResult], output_path: str | Path) -> Path:
    """Export selected calculated results to a single Excel workbook."""
    if not results:
        raise ValueError("No selected SMP results to export.")

    try:
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font, PatternFill
        from openpyxl.utils import get_column_letter
    except ImportError as exc:
        raise RuntimeError("openpyxl is required for XLS export. Run: python -m pip install -r requirements.txt") from exc

    output = Path(output_path)
    if output.suffix.lower() != ".xlsx":
        output = output.with_suffix(".xlsx")

    workbook = Workbook()
    summary = workbook.active
    summary.title = "Summary"

    header_fill = PatternFill("solid", fgColor="E0ECFF")
    title_font = Font(bold=True, size=12, color="111827")
    header_font = Font(bold=True, color="111827")
    center_alignment = Alignment(horizontal="center")

    _write_summary_sheet(summary, results, header_fill, header_font, title_font, center_alignment)

    used_names = {"Summary"}
    for result in results:
        sheet = workbook.create_sheet(_safe_sheet_name(result.metadata.get("file_name") or result.sample_name, used_names))
        _write_result_sheet(sheet, result, header_fill, header_font, title_font, center_alignment)

    for sheet in workbook.worksheets:
        _autofit_columns(sheet, get_column_letter)

    output.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(output)
    return output


def _write_summary_sheet(sheet, results, header_fill, header_font, title_font, center_alignment) -> None:
    sheet["A1"] = "Mercury Intrusion Export"
    sheet["A1"].font = title_font
    sheet["A3"] = "Note"
    sheet["B3"] = "Only selected visible samples are exported. dV/dlogD uses the smoothed intrusion distribution."

    headers = [
        "File",
        "Sample name",
        "Operator",
        "Submitter",
        "Created",
        "Modified",
        "Instrument",
        "Software",
        "Advancing angle (deg)",
        "Surface tension (dynes/cm)",
        "Mercury temperature (C)",
        "Mercury density (g/mL)",
        "Total intrusion volume (mL/g)",
        "Total pore area (m2/g)",
        "Median pore diameter, volume (nm)",
        "Median pore pressure, volume (psia)",
        "Median pore diameter, area (nm)",
        "Median pore pressure, area (psia)",
        "Average pore diameter 4V/A (nm)",
        "Bulk density at 0.50 psia (g/mL)",
        "Apparent skeletal density (g/mL)",
        "Porosity (%)",
        "Max pressure (psia)",
        "Data points",
    ]
    start_row = 5
    for col, header in enumerate(headers, start=1):
        cell = sheet.cell(start_row, col, header)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = center_alignment

    for row, result in enumerate(results, start=start_row + 1):
        summary_metrics_result = summary_metrics(result)
        sheet.cell(row, 1, _text(result.metadata.get("file_name")))
        sheet.cell(row, 2, _text(result.metadata.get("sample_name")))
        sheet.cell(row, 3, _text(result.metadata.get("operator")))
        sheet.cell(row, 4, _text(result.metadata.get("submitter")))
        sheet.cell(row, 5, _text(result.metadata.get("created")))
        sheet.cell(row, 6, _text(result.metadata.get("modified")))
        sheet.cell(row, 7, _text(result.metadata.get("instrument_name")))
        sheet.cell(row, 8, _software_text(result))
        sheet.cell(row, 9, _number(result.metadata.get("adv_contact_angle_deg")))
        sheet.cell(row, 10, _number(result.metadata.get("surface_tension_dynes_cm")))
        sheet.cell(row, 11, _number(result.metadata.get("mercury_temperature_C")))
        sheet.cell(row, 12, _number(result.metadata.get("mercury_density_gmL")))
        sheet.cell(row, 13, summary_metrics_result.total_intrusion_volume)
        sheet.cell(row, 14, summary_metrics_result.total_pore_area)
        sheet.cell(row, 15, summary_metrics_result.median_volume_diameter)
        sheet.cell(row, 16, summary_metrics_result.median_volume_pressure)
        sheet.cell(row, 17, summary_metrics_result.median_area_diameter)
        sheet.cell(row, 18, summary_metrics_result.median_area_pressure)
        sheet.cell(row, 19, summary_metrics_result.average_pore_diameter)
        sheet.cell(row, 20, summary_metrics_result.bulk_density)
        sheet.cell(row, 21, summary_metrics_result.apparent_density)
        sheet.cell(row, 22, summary_metrics_result.porosity)
        sheet.cell(row, 23, result.max_pressure)
        sheet.cell(row, 24, result.data_point_count)

    sheet.freeze_panes = "A6"
    sheet.auto_filter.ref = f"A{start_row}:X{start_row + len(results)}"


def _write_result_sheet(sheet, result: MercuryResult, header_fill, header_font, title_font, center_alignment) -> None:
    sheet["A1"] = _text(result.metadata.get("file_name"))
    sheet["A1"].font = title_font
    summary_metrics_result = summary_metrics(result)

    metadata_rows = [
        ("Sample name", _text(result.metadata.get("sample_name"))),
        ("Operator", _text(result.metadata.get("operator"))),
        ("Submitter", _text(result.metadata.get("submitter"))),
        ("Created", _text(result.metadata.get("created"))),
        ("Modified", _text(result.metadata.get("modified"))),
        ("Instrument", _text(result.metadata.get("instrument_name"))),
        ("Software", _software_text(result)),
        ("Advancing angle (deg)", _number(result.metadata.get("adv_contact_angle_deg"))),
        ("Surface tension (dynes/cm)", _number(result.metadata.get("surface_tension_dynes_cm"))),
        ("Mercury temperature (C)", _number(result.metadata.get("mercury_temperature_C"))),
        ("Mercury density (g/mL)", _number(result.metadata.get("mercury_density_gmL"))),
        ("Total intrusion volume (mL/g)", summary_metrics_result.total_intrusion_volume),
        ("Total pore area (m2/g)", summary_metrics_result.total_pore_area),
        ("Median pore diameter, volume (nm)", summary_metrics_result.median_volume_diameter),
        ("Median pore pressure, volume (psia)", summary_metrics_result.median_volume_pressure),
        ("Median pore diameter, area (nm)", summary_metrics_result.median_area_diameter),
        ("Median pore pressure, area (psia)", summary_metrics_result.median_area_pressure),
        ("Average pore diameter 4V/A (nm)", summary_metrics_result.average_pore_diameter),
        ("Bulk density at 0.50 psia (g/mL)", summary_metrics_result.bulk_density),
        ("Apparent skeletal density (g/mL)", summary_metrics_result.apparent_density),
        ("Porosity (%)", summary_metrics_result.porosity),
        ("Max pressure (psia)", result.max_pressure),
    ]
    for row, (name, value) in enumerate(metadata_rows, start=3):
        sheet.cell(row, 1, name).font = header_font
        sheet.cell(row, 2, value)

    table_row = len(metadata_rows) + 5
    headers = [
        "Point",
        "Cycle",
        "Pressure (psia)",
        "Pore diameter (nm)",
        "Cumulative pore volume (mL/g)",
        "Incremental pore volume (mL/g)",
        "dV/dlogD (smoothed, intrusion only)",
        "% Total intrusion volume",
        "% Incremental intrusion volume",
    ]
    for col, header in enumerate(headers, start=1):
        cell = sheet.cell(table_row, col, header)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = center_alignment

    for row_index, row_data in enumerate(result.table, start=table_row + 1):
        is_extrusion = row_data["is_extrusion"] >= 0.5
        sheet.cell(row_index, 1, row_index - table_row)
        sheet.cell(row_index, 2, "Extrusion" if is_extrusion else "Intrusion")
        sheet.cell(row_index, 3, row_data["pressure"])
        sheet.cell(row_index, 4, row_data["diameter"])
        sheet.cell(row_index, 5, row_data["cum_volume"])
        sheet.cell(row_index, 6, row_data["incremental_volume"])
        sheet.cell(row_index, 7, None if is_extrusion else max(0.0, row_data["log_diff_intrusion_smooth"]))
        sheet.cell(row_index, 8, row_data["pct_total"])
        sheet.cell(row_index, 9, row_data["pct_incremental"])

    last_row = table_row + len(result.table)
    sheet.freeze_panes = f"A{table_row + 1}"
    sheet.auto_filter.ref = f"A{table_row}:I{last_row}"


def _safe_sheet_name(value, used_names: set[str]) -> str:
    base = _text(value) or "Sample"
    base = re.sub(r"[\[\]:*?/\\]", "_", base).strip("'") or "Sample"
    base = base[:31]
    name = base
    counter = 2
    while name in used_names:
        suffix = f"_{counter}"
        name = f"{base[:31 - len(suffix)]}{suffix}"
        counter += 1
    used_names.add(name)
    return name


def _autofit_columns(sheet, get_column_letter) -> None:
    for column_cells in sheet.columns:
        column_letter = get_column_letter(column_cells[0].column)
        max_length = 0
        for cell in column_cells:
            value = "" if cell.value is None else str(cell.value)
            max_length = max(max_length, len(value))
        sheet.column_dimensions[column_letter].width = min(max(max_length + 2, 10), 42)


def _text(value) -> str:
    text = str(value or "").strip()
    return text if text else "NA"


def _software_text(result: MercuryResult) -> str:
    name = str(result.metadata.get("analysis_software") or "").strip()
    version = str(result.metadata.get("analysis_software_version") or "").strip()
    if name and version:
        return f"{name} {version}"
    if name:
        return name
    return _text(result.metadata.get("software_version"))


def _number(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
