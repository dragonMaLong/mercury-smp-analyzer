from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from mercury_app.core import export_microactive_csv, load_smp, summary_metrics


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Mercury intrusion SMP MVP")
    parser.add_argument("smp", nargs="?", help="Path to a .SMP file")
    parser.add_argument("--csv", help="Optional CSV output path")
    parser.add_argument("--ui", action="store_true", help="Launch the optional Qt/pyqtgraph UI")
    args = parser.parse_args(argv)

    if args.ui or not args.smp:
        return _run_ui_or_explain()

    result = load_smp(args.smp)
    _print_cli_summary(result)

    if args.csv:
        export_microactive_csv(result, args.csv)
        print(f"CSV: {Path(args.csv).resolve()}")

    return 0


def _run_ui_or_explain() -> int:
    os.environ.setdefault("PYQTGRAPH_QT_LIB", "PyQt5")
    try:
        from mercury_app.ui.main_window import run
    except ModuleNotFoundError as exc:
        if exc.name in {"PyQt5", "pyqtgraph"}:
            print("GUI dependencies are not installed.")
            print("Install them with: python -m pip install PyQt5 pyqtgraph")
            print("CLI still works, for example: python app.py M5G2.SMP")
            return 2
        raise

    return run()


def _print_cli_summary(result) -> None:
    meta = result.metadata
    summary = summary_metrics(result)
    print(f"File: {meta.get('file_path', '')}")
    print(f"Sample: {result.sample_name}")
    print(f"Mass: {meta.get('sample_mass_g', 0.0):.6g} g")
    print(f"Penetrometer: {meta.get('penetrometer_model', '')}")
    print(f"Penetrometer constant: {meta.get('penetrometer_constant_uL_per_pF', 0.0):.6g} uL/pF")
    print(f"Total intrusion volume: {summary.total_intrusion_volume:.9g} mL/g")
    print(f"Total pore area: {summary.total_pore_area:.9g} m2/g")
    print(f"Median pore diameter (volume): {summary.median_volume_diameter:.9g} nm")
    print(f"Median pore diameter (area): {summary.median_area_diameter:.9g} nm")
    print(f"Average pore diameter (4V/A): {summary.average_pore_diameter:.9g} nm")
    print(f"Bulk density at 0.50 psia: {summary.bulk_density:.9g} g/mL")
    print(f"Apparent skeletal density: {summary.apparent_density:.9g} g/mL")
    print(f"Porosity: {summary.porosity:.9g} %")
    print(f"Max pressure: {result.max_pressure:.9g} psia")
    print(f"Data points: {result.data_point_count}")


if __name__ == "__main__":
    raise SystemExit(main())
