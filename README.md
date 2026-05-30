# Mercury SMP Analyzer

A desktop MVP for viewing and comparing Micromeritics AutoPore / MicroActive `.SMP` mercury intrusion files.

The goal of this project is to provide a lightweight single-file analysis workflow: open one or more SMP files, calculate MicroActive-like pore data, compare pore size distributions, adjust key Washburn parameters, and export selected results to Excel.

## Features

- Open one or more `.SMP` files.
- Add more SMP files without replacing the current list.
- Show sample metadata, test time, instrument, software version, operator, submitter, penetrometer constant, mass, MicroActive-like summary metrics, and max pressure.
- Plot:
  - Pore size distribution, displayed as the main chart with log-diameter Akima curve interpolation.
  - Pressure vs cumulative pore volume.
- Toggle each sample on or off with the blue dot.
- Sort loaded files by test time.
- Edit advancing contact angle and mercury surface tension in the sample table.
- Recalculate pore diameter and pore size distribution immediately after parameter edits.
- Select a pressure range interactively and update pore range, pore volume share, and peak diameter metrics.
- Export all selected samples to one `.xlsx` workbook.

## Project Structure

```text
mercury_smp_app/
  app.py
  requirements.txt
  smp_parser.py
  mercury_app/
    core/
      excel_export.py
      metrics.py
      microactive_calc.py
      models.py
      smp_parser.py
    ui/
      main_window.py
      plots.py
  tests/
    test_smp_vs_xls.py
  SMP文件格式完整解析总结.md
```

## Install

Python 3.10+ is recommended.

```powershell
python -m pip install -r requirements.txt
```

## Run The UI

```powershell
python app.py --ui
```

If you keep SMP files outside this folder, you can still select them from the file dialog.

## Run From The Command Line

```powershell
python app.py D:\path\to\sample.SMP
```

Optional CSV export:

```powershell
python app.py D:\path\to\sample.SMP --csv output.csv
```

## Excel Export

Use `Export XLS` in the UI. The software exports only samples whose blue dot is selected.

The workbook contains:

- `Summary`: one row per selected sample.
- One sheet per sample.
- Pressure, pore diameter, cumulative pore volume, incremental pore volume, smoothed `dV/dlogD`, and percentage columns.

Raw unsmoothed differential columns are intentionally not exported by default to keep the workbook clean.

## Calculation Notes

Pore diameter is calculated with the Washburn equation using:

- Advancing contact angle
- Mercury surface tension
- Corrected pressure

The SMP file may contain these method parameters. When valid values are present, the app uses the values stored in the SMP file. If a valid value is not found, the parser falls back to common Micromeritics-style defaults:

- Advancing contact angle: `130°`
- Surface tension: `485 dynes/cm`

The UI lets you temporarily override contact angle and surface tension per sample. These edits are kept in memory and do not modify the original SMP file.

For display, the pore size distribution uses the smoothed `dV/dlogD` values and draws a dense Akima-interpolated curve over `log10(pore diameter)`. The circle markers remain visible as the calculated data points.

Summary metrics follow the report-style calculations used by MicroActive where practical:

- Total pore area uses interval pore area, `4000 * incremental volume / midpoint pore diameter`.
- Median pore diameters are interpolated at half total intrusion volume or half total pore area, then converted through the same pressure-diameter relationship.
- Average pore diameter uses `4V/A`.
- Apparent skeletal density and porosity are derived from the calculated bulk density and total intrusion volume.

## Tests

The tests use local SMP/XLS reference files when available. If sample files are not present next to the project folder, data-dependent tests are skipped.

```powershell
pytest -q
```

## Status

This is an MVP focused on the core single-file and multi-file viewing workflow. It is not yet a full replacement for MicroActive reports, database workflows, or batch production reporting.
