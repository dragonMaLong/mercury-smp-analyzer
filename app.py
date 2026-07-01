from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from mercury_app.core import export_microactive_csv, load_smp, summary_metrics


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="压汞 SMP 分析工具")
    parser.add_argument("smp", nargs="?", help="SMP 文件路径")
    parser.add_argument("--csv", help="可选的 CSV 导出路径")
    parser.add_argument("--ui", action="store_true", help="启动 Qt/pyqtgraph 图形界面")
    args = parser.parse_args(argv)

    if args.ui or not args.smp:
        return _run_ui_or_explain()

    result = load_smp(args.smp)
    _print_cli_summary(result)

    if args.csv:
        export_microactive_csv(result, args.csv)
        print(f"CSV 文件: {Path(args.csv).resolve()}")

    return 0


def _run_ui_or_explain() -> int:
    os.environ.setdefault("PYQTGRAPH_QT_LIB", "PyQt5")
    try:
        from mercury_app.ui.main_window import run
    except ModuleNotFoundError as exc:
        if exc.name in {"PyQt5", "pyqtgraph"}:
            print("尚未安装图形界面依赖。")
            print("请运行：python -m pip install PyQt5 pyqtgraph")
            print("命令行模式仍可使用，例如：python app.py M5G2.SMP")
            return 2
        raise

    return run()


def _print_cli_summary(result) -> None:
    meta = result.metadata
    summary = summary_metrics(result)
    print(f"文件: {meta.get('file_path', '')}")
    print(f"样品: {result.sample_name}")
    print(f"样品质量: {meta.get('sample_mass_g', 0.0):.6g} g")
    print(f"膨胀计: {meta.get('penetrometer_model', '')}")
    print(f"膨胀计常数: {meta.get('penetrometer_constant_uL_per_pF', 0.0):.6g} uL/pF")
    print(f"总入汞体积: {summary.total_intrusion_volume:.9g} mL/g")
    print(f"总孔面积: {summary.total_pore_area:.9g} m2/g")
    print(f"中值孔径（体积）: {summary.median_volume_diameter:.9g} nm")
    print(f"中值孔径（面积）: {summary.median_area_diameter:.9g} nm")
    print(f"平均孔径（4V/A）: {summary.average_pore_diameter:.9g} nm")
    print(f"0.50 psia 体积密度: {summary.bulk_density:.9g} g/mL")
    print(f"表观骨架密度: {summary.apparent_density:.9g} g/mL")
    print(f"孔隙率: {summary.porosity:.9g} %")
    print(f"最大压力: {result.max_pressure:.9g} psia")
    print(f"数据点数: {result.data_point_count}")


if __name__ == "__main__":
    raise SystemExit(main())
