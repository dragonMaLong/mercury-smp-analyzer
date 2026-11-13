"""PDF summary report: information/result tables and overlay figures, no point data.

The caller (MainWindow) supplies already-computed content, so the PDF shows the
same fits and settings as the screen. Rendering uses only Qt and pyqtgraph,
which the application already bundles.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import html
import math
import os
from pathlib import Path
import sys
import tempfile

import numpy as np
os.environ.setdefault("PYQTGRAPH_QT_LIB", "PyQt5")
import pyqtgraph as pg
from pyqtgraph.Qt import QtCore, QtGui, QtWidgets

SAMPLES_PER_TABLE = 4
PDF_DPI = 300
BODY_PT = 7.5  # matches Micromeritics report text (about 7.5 pt); headings 1-1.5 pt larger
BRAND_NAME = "DragonScience"
WEBSITE = "www.dragonscience.top"
LOGO_PATH = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2])) / "assets" / "dragonscience-logo.png"
FIGURE_WIDTH_FRACTION = 0.70  # figure width relative to the text width; two parameter+figure blocks per page


@dataclass
class Series:
    label: str
    color: str
    x: list[float]
    y: list[float]
    style: str = "line"  # "line", "dash", "points", "fit" (thin line, no markers, not in legend)


@dataclass
class Figure:
    title: str
    x_label: str
    y_label: str
    series: list[Series] = field(default_factory=list)
    log_x: bool = False
    note: str = ""
    params: "Table | None" = None  # method parameters printed above the figure


@dataclass
class Table:
    title: str
    row_labels: list[str]
    columns: list[tuple[str, list[str]]]  # (sample label, values aligned with row_labels)
    note: str = ""


@dataclass
class Report:
    title: str
    subtitle: str
    tables: list[Table]
    figures: list[Figure]
    footnote: str = ""


def _finite_pairs(xs, ys, positive_x=False):
    out_x, out_y = [], []
    for x, y in zip(xs, ys):
        try:
            x, y = float(x), float(y)
        except (TypeError, ValueError):
            continue
        if math.isfinite(x) and math.isfinite(y) and (x > 0 or not positive_x):
            out_x.append(x)
            out_y.append(y)
    return out_x, out_y


def render_figure(figure: Figure, width: int = 1100, height: int = 620) -> QtGui.QImage | None:
    """Draw one overlay chart offscreen; None when no series has data."""
    widget = pg.PlotWidget(background="w")
    widget.scene().setItemIndexMethod(QtWidgets.QGraphicsScene.NoIndex)
    widget.setAttribute(QtCore.Qt.WA_DontShowOnScreen, True)  # lay out at real size, never visible
    widget.resize(width, height)
    plot = widget.getPlotItem()
    tick_font = QtGui.QFont("Microsoft YaHei")
    tick_font.setPixelSize(13)
    plot.setTitle(figure.title, color="#111111", size="17px")
    for axis_name, label in (("bottom", figure.x_label), ("left", figure.y_label)):
        axis = plot.getAxis(axis_name)
        axis.setPen(pg.mkPen("#333333"))
        axis.setTextPen(pg.mkPen("#222222"))
        axis.setLabel(label, color="#222222", **{"font-size": "15px"})
        axis.enableAutoSIPrefix(False)
        axis.setStyle(tickFont=tick_font)
    plot.showGrid(x=True, y=True, alpha=0.25)
    legend = plot.addLegend(offset=(-10, 10), labelTextColor="#111111", labelTextSize="13px")
    legend.setBrush(pg.mkBrush(255, 255, 255, 220))
    drawn = 0
    all_x, all_y = [], []
    for series in figure.series:
        xs, ys = _finite_pairs(series.x, series.y, positive_x=figure.log_x)
        if not xs:
            continue
        all_x.extend(xs)
        all_y.extend(ys)
        color = QtGui.QColor(series.color)
        name = None if series.style == "fit" or not series.label else series.label
        if series.style == "points":
            # PlotDataItem applies PlotItem's logarithmic transformation to the
            # points too. A bare ScatterPlotItem leaves them at linear x values.
            item = pg.PlotDataItem(xs, ys, pen=None, symbol="o", symbolSize=7,
                                  symbolPen=color, symbolBrush=color, name=name)
        else:
            style = QtCore.Qt.DashLine if series.style == "dash" else QtCore.Qt.SolidLine
            pen_width = 1.4 if series.style == "fit" else 2.2
            item = pg.PlotDataItem(xs, ys, pen=pg.mkPen(color, width=pen_width, style=style), name=name,
                                   symbol="o" if series.style == "line" and len(xs) <= 60 else None,
                                   symbolSize=5, symbolBrush=color, symbolPen=color)
        plot.addItem(item)
        drawn += series.style != "fit"
    if not drawn:
        widget.deleteLater()
        return None
    if figure.log_x:
        plot.setLogMode(x=True, y=False)
        # The chart is printed at 70% page width; labelling every logarithmic
        # minor tick makes the exported labels collide at that physical size.
        lo, hi = math.log10(min(all_x)), math.log10(max(all_x))
        ticks = []
        for exponent in range(math.floor(lo), math.ceil(hi) + 1):
            for multiplier in ((1, 2, 5) if hi - lo < 2 else (1,)):
                value = multiplier * 10.0 ** exponent
                position = math.log10(value)
                if lo - 1e-9 <= position <= hi + 1e-9:
                    ticks.append((position, f"{value:g}"))
        plot.getAxis("bottom").setTicks([ticks])
    widget.show()
    # QGraphicsScene posts its initial polish/indexing work. Flush only this
    # temporary scene, never the application's unrelated windows and timers.
    QtWidgets.QApplication.sendPostedEvents(widget.scene())
    plot.layout.activate()
    x_bounds = [math.log10(min(all_x)), math.log10(max(all_x))] if figure.log_x else [min(all_x), max(all_x)]
    y_bounds = [min(all_y), max(all_y)]
    # Set ranges from the snapshot directly; autoRange can see temporary item
    # transforms before the first offscreen paint and compute an incorrect range.
    plot.setRange(xRange=x_bounds, yRange=y_bounds, padding=.05, disableAutoRange=True)
    plot.vb.prepareForPaint()
    # grab() lays out and paints the widget synchronously. Pumping the global
    # event loop here can run unrelated selection timers or deferred destruction
    # of a previously closed window in the middle of this report snapshot.
    image = widget.grab().toImage()
    image.setDevicePixelRatio(1.0)
    if (image.width(), image.height()) != (width, height):  # high-DPI screens grab at a larger scale
        image = image.scaled(width, height, QtCore.Qt.IgnoreAspectRatio, QtCore.Qt.SmoothTransformation)
    widget.close()
    widget.deleteLater()
    return image


def _table_chunks(table: Table, padding: int = 8) -> list[str]:
    """HTML for each group of samples; the first carries title and note, later ones '(续)'."""
    chunks = []
    for start in range(0, max(len(table.columns), 1), SAMPLES_PER_TABLE):
        chunk = table.columns[start:start + SAMPLES_PER_TABLE]
        title = table.title if start == 0 else f"{table.title}（续）"
        parts = [f"<h3>{html.escape(title)}</h3>"]
        if table.note and start == 0:
            parts.append(f"<p class='note'>{html.escape(table.note)}</p>")
        head = "".join(f"<th>{html.escape(label)}</th>" for label, _values in chunk)
        rows = []
        for index, row_label in enumerate(table.row_labels):
            cells = "".join(f"<td>{html.escape(str(values[index]) if index < len(values) else '')}</td>"
                            for _label, values in chunk)
            rows.append(f"<tr><td class='key'>{html.escape(row_label)}</td>{cells}</tr>")
        parts.append(f"<table cellspacing='0' cellpadding='{padding}'><tr><th class='key'>项目</th>{head}</tr>{''.join(rows)}</table>")
        chunks.append("".join(parts))
    return chunks


def _table_html(table: Table, padding: int = 8) -> str:
    return "<br/>".join(_table_chunks(table, padding))


def _body_font(points: float, bold: bool = False) -> QtGui.QFont:
    font = QtGui.QFont("Microsoft YaHei")
    font.setPointSizeF(points)
    font.setBold(bold)
    return font


def _text_document(writer, width: float, mm: float, html_body: str):
    document = QtGui.QTextDocument()
    document.documentLayout().setPaintDevice(writer)
    document.setDocumentMargin(0)
    document.setTextWidth(width)  # one block at its natural height; pages are laid out by write_pdf
    document.setDefaultFont(_body_font(BODY_PT))
    gap = int(2.5 * mm)
    document.setDefaultStyleSheet(
        f"h3{{font-size:{BODY_PT + 1}pt;font-weight:bold;color:#0f2a44;margin-top:{gap}px;margin-bottom:{gap // 3}px;}}"
        f"p{{margin-top:0px;margin-bottom:{gap // 3}px;}} p.note{{color:#555555;font-size:{BODY_PT - 0.5}pt;}}"
        "table{border-collapse:collapse;width:100%;} th{background-color:#e8eef4;border:1px solid #9aa9b8;font-weight:bold;}"
        "td{border:1px solid #c3ccd5;} td.key,th.key{background-color:#f4f6f8;font-weight:bold;}"
    )
    document.setHtml("<html><body>" + html_body + "</body></html>")
    return document


def write_pdf(report: Report, path: str | Path) -> int:
    """Atomically save a PDF, preserving an existing report if rendering fails."""
    if QtWidgets.QApplication.instance() is None:
        raise RuntimeError("PDF 导出需要已启动的图形界面。")
    _ensure_report_fonts()
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{output.stem}-", suffix=".pdf", dir=output.parent)
    os.close(fd)
    try:
        drawn = _write_pdf(report, temporary)
        os.replace(temporary, output)
        return drawn
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _ensure_report_fonts() -> None:
    """Qt's Windows offscreen plugin does not discover installed system fonts."""
    if "Microsoft YaHei" in QtGui.QFontDatabase().families():
        return
    fonts = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
    for filename in ("msyh.ttc", "msyhbd.ttc"):
        path = fonts / filename
        if path.is_file():
            QtGui.QFontDatabase.addApplicationFont(str(path))


def _write_pdf(report: Report, path: str | Path) -> int:
    """Write the report; returns the number of figures drawn.

    Laid out directly on the PDF device (its own resolution and page size), so
    font sizes do not follow the Windows display-scaling setting. Every page
    carries the DragonScience header (logo, title, website) and a page footer.
    Text sizes follow the Micromeritics report style (about 7.5 pt). Each
    figure is preceded by a table of the method parameters used for it.
    """
    writer = QtGui.QPdfWriter(str(path))
    writer.setPageSize(QtGui.QPageSize(QtGui.QPageSize.A4))
    writer.setPageMargins(QtCore.QMarginsF(15, 10, 15, 10), QtGui.QPageLayout.Millimeter)
    writer.setResolution(PDF_DPI)
    writer.setTitle(report.title)
    writer.setCreator(f"{BRAND_NAME} {WEBSITE}")
    page_width, page_height = writer.width(), writer.height()
    mm = PDF_DPI / 25.4
    header = int(20 * mm)
    footer = int(6 * mm)
    content_height = page_height - header - footer

    padding = int(0.3 * mm)
    spacing = 3 * mm
    figure_width = page_width * FIGURE_WIDTH_FRACTION
    note_height = int(4 * mm)

    # Every item is an unbreakable block: a table with its heading, or a figure
    # with its parameter table. A block that does not fit moves to the next page.
    blocks = []  # dicts: text (QTextDocument) / image / note / height / new_page
    def text_block(markup, new_page=False):
        document = _text_document(writer, page_width, mm, markup)
        blocks.append({"text": document, "height": document.size().height(), "new_page": new_page})

    text_block(f"<p class='note' align='center'>{html.escape(report.subtitle)}</p>")
    for table in report.tables:
        for chunk in _table_chunks(table, padding):
            text_block(chunk)
    if report.footnote:
        text_block(f"<p class='note'>{html.escape(report.footnote)}</p>")
    figures_start = True
    drawn = 0
    for figure in report.figures:
        image = render_figure(figure)
        if image is None:
            continue
        params = None
        if figure.params is not None and figure.params.columns:
            chunks = _table_chunks(figure.params, padding=padding)
            params = _text_document(writer, page_width, mm, "<br/>".join(chunks))
            estimated_image_height = figure_width * image.height() / image.width()
            if params.size().height() + estimated_image_height + 1.5 * mm > content_height:
                # Large batches can exceed a full page. Paginate earlier
                # parameter groups, leaving the last group beside the figure.
                for chunk in chunks[:-1]:
                    text_block(chunk, new_page=figures_start)
                    figures_start = False
                params = _text_document(writer, page_width, mm, chunks[-1])
        image_height = figure_width * image.height() / image.width()
        params_height = params.size().height() + 1.5 * mm if params else 0
        blocks.append({"text": params, "image": image, "image_height": image_height, "note": figure.note,
                       "params_height": params_height, "new_page": figures_start,
                       "height": params_height + image_height + (note_height if figure.note else 0)})
        figures_start = False  # figures begin on a fresh page, as in the vendor report
        drawn += 1

    page, y = 0, 0.0
    for block in blocks:
        if block["new_page"] and y > 0 or y > 0 and y + block["height"] > content_height:
            page, y = page + 1, 0.0
        block["page"], block["top"] = page, y
        y += block["height"] + spacing
    total = page + 1
    logo = QtGui.QImage(str(LOGO_PATH)) if LOGO_PATH.exists() else QtGui.QImage()
    painter = QtGui.QPainter(writer)
    if not painter.isActive():
        raise OSError(f"无法创建 PDF：{path}")
    try:
        painter.setRenderHint(QtGui.QPainter.SmoothPixmapTransform)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)

        def frame(index):
            # Header: DragonScience logo centred, report title, website; thin rule under it.
            logo_height = 7.5 * mm
            if not logo.isNull():
                logo_width = logo_height * logo.width() / logo.height()
                painter.drawImage(QtCore.QRectF((page_width - logo_width) / 2, 0, logo_width, logo_height), logo)
            painter.setPen(QtGui.QColor("#0f2a44"))
            painter.setFont(_body_font(BODY_PT + 1.5, bold=True))
            painter.drawText(QtCore.QRectF(0, logo_height + 1.0 * mm, page_width, 4.5 * mm),
                             QtCore.Qt.AlignHCenter | QtCore.Qt.AlignVCenter, report.title)
            painter.setFont(_body_font(BODY_PT - 0.5))
            painter.setPen(QtGui.QColor("#2a6f97"))
            painter.drawText(QtCore.QRectF(0, logo_height + 5.2 * mm, page_width, 3.5 * mm),
                             QtCore.Qt.AlignHCenter | QtCore.Qt.AlignVCenter, WEBSITE)
            painter.setPen(QtGui.QPen(QtGui.QColor("#9aa9b8"), max(1, int(0.2 * mm))))
            painter.drawLine(QtCore.QPointF(0, header - 1.2 * mm), QtCore.QPointF(page_width, header - 1.2 * mm))
            # Footer: website left, page number right.
            painter.setFont(_body_font(BODY_PT - 0.5))
            painter.setPen(QtGui.QColor("#666666"))
            footer_rect = QtCore.QRectF(0, page_height - footer, page_width, footer)
            painter.drawText(footer_rect, QtCore.Qt.AlignLeft | QtCore.Qt.AlignBottom, WEBSITE)
            painter.drawText(footer_rect, QtCore.Qt.AlignRight | QtCore.Qt.AlignBottom, f"{index + 1} / {total}")

        current = -1
        for block in blocks:
            if block["page"] != current:
                if current >= 0:
                    if not writer.newPage():
                        raise OSError("PDF 分页写入失败。")
                current = block["page"]
                frame(current)
            y = header + block["top"]
            text = block.get("text")
            if text is not None:
                painter.save()
                painter.translate(0, y)
                text.drawContents(painter, QtCore.QRectF(0, 0, page_width, text.size().height()))
                painter.restore()
            if "image" in block:
                y += block["params_height"]
                target = QtCore.QRectF((page_width - figure_width) / 2, y, figure_width, block["image_height"])
                painter.drawImage(target, block["image"])
                if block["note"]:
                    painter.setFont(_body_font(BODY_PT - 0.5))
                    painter.setPen(QtGui.QColor("#555555"))
                    painter.drawText(QtCore.QRectF(target.left(), target.bottom(), figure_width, note_height),
                                     QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, block["note"])
    finally:
        painter.end()
    return drawn


def report_subtitle(sample_count: int, version: str) -> str:
    return f"生成时间：{datetime.now():%Y-%m-%d %H:%M}    样品数：{sample_count}    软件版本：{version}"


def _value(value, digits: int = 6) -> str:
    if value is None or value == "":
        return "未记录"
    if isinstance(value, (float, int, np.number)):
        return f"{value:.{digits}g}" if math.isfinite(float(value)) else "—"
    return str(value)


def build_report_from_window(window, selected_results, version: str = "", *, include_figures: bool = True) -> Report:
    """Take a synchronous snapshot of current selections and per-sample inputs.

    Recompute using the exact UI functions and current bounds, so a just-dragged
    selection is not exported from a stale coalesced-timer cache. No screen cells
    are used: sorting/transposing the result table cannot misassociate a sample.
    """
    from .metrics import metrics_for_pressure_range, summary_metrics
    from .mayer_stowe import calculate_mayer_stowe
    from .pore_structure import calculate_pore_structure

    results = list(selected_results)
    if not results:
        raise ValueError("没有选中的 SMP 结果可导出。")
    labels = [window._sample_row_label(result) for result in results]
    pressure_range = window._current_pressure_region() or (2900.0, 9100.0)
    mayer_range = window._current_mayer_pressure_region() or (400.0, 10000.0)
    pore_range = window._current_pore_structure_pressure_region() or pressure_range
    summaries = [summary_metrics(result) for result in results]
    segments = [metrics_for_pressure_range(result, *pressure_range) for result in results]
    options = [window._pore_options_for_result(result) for result in results]
    pores = [calculate_pore_structure(result, option, *pore_range) for result, option in zip(results, options)]
    mayers = [calculate_mayer_stowe(result, *mayer_range) for result in results]

    def table(title, rows, values, note=""):
        return Table(title, rows, [(label, [_value(value) for value in sample_values])
                                   for label, sample_values in zip(labels, values)], note)

    information = table("样品信息", ["文件", "样品名称", "测试人员", "提交者", "测试时间", "测试仪器", "测试软件", "样品质量 (g)", "膨胀计常数 (μL/pF)", "数据点数"],
        [[r.metadata.get("file_name"), r.sample_name, r.metadata.get("operator"), r.metadata.get("submitter"),
          r.metadata.get("created"), r.metadata.get("instrument_name"),
          " ".join(str(r.metadata.get(key) or "") for key in ("analysis_software", "analysis_software_version")).strip() or r.metadata.get("software_version"),
          r.metadata.get("sample_mass_g"), r.metadata.get("penetrometer_constant_uL_per_pF"), r.data_point_count] for r in results])
    summary = table("基础参数", ["进汞接触角 (deg)", "汞表面张力 (dynes/cm)", "总入汞体积 (mL/g)", "压汞比表面积 (m²/g)",
        "中值孔径（体积）(nm)", "中值孔径（面积）(nm)", "平均孔径 4V/A (nm)", "体积密度 (g/mL)", "体积密度对应压力 (psia)",
        "骨架密度 (g/mL)", "孔隙率 (%)", "最大压力 (psia)"],
        [[r.metadata.get("adv_contact_angle_deg"), r.metadata.get("surface_tension_dynes_cm"), s.total_intrusion_volume, s.total_pore_area,
          s.median_volume_diameter, s.median_area_diameter, s.average_pore_diameter, s.bulk_density, s.bulk_density_pressure,
          s.apparent_density, s.porosity, r.max_pressure] for r, s in zip(results, summaries)])
    selection = table("选区孔容", [name for name, _ in segments[0].as_display_rows()],
        [[value for _, value in segment.as_display_rows()] for segment in segments])
    mayer = table("Mayer-Stowe", ["选区起点 (psia)", "选区终点 (psia)", "选区孔容 (mL/g)", "体积密度 (g/mL)",
        "粒间孔隙率（原始）", "粒间孔隙率（查表）", "Break-through pressure ratio (K_MS)", "状态"],
        [[m.pressure_min, m.pressure_max, m.selected_pore_volume, m.bulk_density, m.interstitial_porosity_raw,
          m.interstitial_porosity, m.breakthrough_pressure_ratio, m.status or "有效"] for m in mayers])
    pore = table("孔结构", ["压汞比表面积 (m²/g)", "压汞体积密度 (g/mL)", "压汞骨架密度 (g/mL)", "渗透率 (mdarcy)",
        "阈值压力 (psia)", "特征长度 (nm)", "传导形成因子", "曲折因子", "曲折度", "计算提示"],
        [[s.total_pore_area, s.bulk_density, s.apparent_density, p.permeability_md, p.threshold_pressure_psia,
          p.characteristic_length_nm, p.conductivity_formation_factor, p.tortuosity_factor, p.tortuosity, p.warning or "有效"]
         for s, p in zip(summaries, pores)])
    inputs = table("孔结构计算参数", ["选区起点 (psia)", "选区终点 (psia)", "比表面积来源", "使用比表面积 (m²/g)", "密度来源",
        "使用体积密度 (g/mL)", "使用骨架密度 (g/mL)", "渗透率常数", "孔形指数", "阈值压力来源", "传导形成因子来源"],
        [[*pore_range, "压汞计算" if o.use_calculated_surface_area else "BET / 手动输入", p.surface_area_m2g,
          "手动输入" if o.use_entered_density else "压汞计算", p.bulk_density_gmL, p.skeletal_density_gmL,
          o.permeability_constant, o.pore_shape_exponent, "自动计算" if p.threshold_is_calculated else "手动输入",
          "自动计算" if p.conductivity_is_calculated else "手动输入"] for o, p in zip(options, pores)])
    figures = []
    if include_figures:
        from mercury_app.ui.plots import distribution_plot_data, smooth_log_distribution_curve

        index_by_id = {id(result): index for index, result in enumerate(window.results)}
        colors = [window.sample_colors[index_by_id[id(r)] % len(window.sample_colors)] for r in results]
        distributions = [distribution_plot_data(r) for r in results]
        distribution_series, pressure_series, cumulative_series = [], [], []
        mayer_incremental, mayer_cumulative = [], []
        for label, color, result, distribution, p, m in zip(labels, colors, results, distributions, pores, mayers):
            distribution_series.append(Series(label, color, distribution["x"].tolist(), distribution["y"].tolist(), "points"))
            distribution_series.append(Series("", color, distribution["curve_x"].tolist(), distribution["curve_y"].tolist(), "fit"))
            for extrusion, name, style in ((False, "入汞", "line"), (True, "退汞", "dash")):
                mask = (result.is_extrusion >= .5) if extrusion else (result.is_extrusion < .5)
                pressure_series.append(Series(f"{label} {name}", color, result.pressure[mask].tolist(), result.cum_volume[mask].tolist(), style))
            cumulative_series.append(Series(label, color, p.pressure.tolist(), p.cumulative_percent.tolist()))
            if m.is_valid:
                for collection, ys in ((mayer_incremental, m.incremental_percent), (mayer_cumulative, m.cumulative_coarser_percent)):
                    xs, smooth_ys = smooth_log_distribution_curve(m.particle_diameter, ys)
                    collection.append(Series(label, color, m.particle_diameter.tolist(), ys.tolist(), "points"))
                    collection.append(Series("", color, xs.tolist(), np.clip(smooth_ys, 0, 100).tolist(), "fit"))

        distribution_params = table("孔径分布参数", ["进汞接触角 (deg)", "汞表面张力 (dynes/cm)", "压力选区 (psia)"],
            [[r.metadata.get("adv_contact_angle_deg"), r.metadata.get("surface_tension_dynes_cm"),
              f"{pressure_range[0]:.6g} - {pressure_range[1]:.6g}"] for r in results])
        mayer_params = table("Mayer-Stowe 参数", ["压力选区 (psia)", "粒间孔隙率", "K_MS", "状态"],
            [[f"{mayer_range[0]:.6g} - {mayer_range[1]:.6g}", m.interstitial_porosity, m.breakthrough_pressure_ratio, m.status or "有效"] for m in mayers])
        pore_params = table("孔结构参数", ["压力选区 (psia)", "阈值压力 (psia)", "渗透率 (mdarcy)", "曲折度"],
            [[f"{pore_range[0]:.6g} - {pore_range[1]:.6g}", p.threshold_pressure_psia, p.permeability_md, p.tortuosity] for p in pores])
        figures = [
            Figure("孔径分布", "孔径 (nm)", "dV/dlogD (mL/g)", distribution_series, True, params=distribution_params),
            Figure("压力 - 累计孔体积", "压力 (psia)", "累计孔体积 (mL/g)", pressure_series, True, params=distribution_params),
            Figure("Mayer-Stowe 增量体积分布", "粒径 (nm)", "增量体积 (%)", mayer_incremental, True, params=mayer_params),
            Figure("Mayer-Stowe 累计体积分布", "粒径 (nm)", "累计体积 (%)", mayer_cumulative, True, params=mayer_params),
            Figure("孔结构累计体积百分比", "压力 (psia)", "累计体积 (%)", cumulative_series, True, params=pore_params),
        ]
    return Report("压汞分析报告", report_subtitle(len(results), version),
                  [information, summary, selection, mayer, pore, inputs], figures,
                  "本报告使用当前样品参数和各分析页的压力选区。未勾选样品不参与导出；详细点数据见 Excel 工作簿。")


def fit_line(slope, intercept, xs) -> tuple[list[float], list[float]]:
    try:
        slope, intercept = float(slope), float(intercept)
    except (TypeError, ValueError):
        return [], []
    xs = [float(x) for x in xs if x is not None and math.isfinite(float(x))]
    if not xs or not (math.isfinite(slope) and math.isfinite(intercept)):
        return [], []
    grid = list(np.linspace(min(xs), max(xs), 50))
    return grid, [slope * x + intercept for x in grid]
