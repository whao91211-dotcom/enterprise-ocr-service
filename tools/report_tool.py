"""Tool5: Report —— 生成销售数据统计报告(Word .docx)。

从已确认数据 SQL 聚合 → 生成结构化 Word 报告:
  概述(单据数/行数/总额) + 按商品/顾客公司/日期的统计表 + 可选统计图。
输出到 reports/ 目录, 供课程汇报/企业交付。

依赖: python-docx
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from uuid import uuid4

from langchain_core.tools import tool

from db import crud

REPORTS_DIR = Path(__file__).resolve().parents[1] / "reports"
_LABEL = {"item": "商品", "desc": "顾客公司", "date": "发注日"}


def _fmt_money(v: float) -> str:
    return f"{v:,.2f}"


@tool
def generate_report(
    year: str = "",
    include_chart: bool = True,
    group_by: str = "item",
    keyword: str = "",
    snapshot_id: str = "",
) -> str:
    """把已确认销售数据生成一份 Word 统计报告(.docx)。

    Args:
        year: 可选年份过滤, 如 "2024" 或 "2024年"; 空=全部年份。
        keyword: 与查询相同的公司/商品/源公司/文件名过滤关键词。
        snapshot_id: 复用其他办公文件返回的快照ID；空则取最新数据。
        include_chart: 是否在报告中嵌入统计图(默认 true)。
        group_by: 报告中明细表的分组维度: item(商品)/desc(顾客公司)/date(发注日)。
    """
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Pt, RGBColor

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    from services.sales_snapshot import capture
    if group_by not in ('item','desc','date'):
        return 'group_by 仅支持: item、desc、date'
    try:
        snapshot = capture(year, keyword, snapshot_id)
    except (ValueError, LookupError, FileNotFoundError) as exc:
        return '错误: '+str(exc)
    year, keyword = snapshot['filters']['year'], snapshot['filters']['keyword']
    total_rows = snapshot['summary']['total_rows']
    if total_rows == 0:
        return '（没有符合条件的已确认数据，无法生成报告）'
    by_item, by_desc, by_date = [snapshot['groups'][key] for key in ('item','desc','date')]
    confirmed_docs = snapshot['summary']['total_docs']
    artifact_id = uuid4().hex
    chart_warning = ''

    title_txt = "销售数据统计报告"
    subtitle = f"（年份: {year}）" if year else "（全部年份）"

    # ---- 构建 Word ----
    doc = Document()
    # 标题
    h = doc.add_heading(title_txt, level=0)
    for run in h.runs:
        run.font.color.rgb = RGBColor(0x0B, 0x5E, 0x97)
    sub = doc.add_paragraph()
    sub.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = sub.add_run(subtitle)
    r.font.size = Pt(12)
    r.font.color.rgb = RGBColor(0x66, 0x66, 0x66)

    if keyword:
        doc.add_paragraph('过滤关键词：'+keyword)
    # 概述
    doc.add_heading("一、数据总览", level=1)
    overview = doc.add_table(rows=0, cols=2)
    overview.style = "Light Grid Accent 1"
    rows = [
        ("统计范围", subtitle.strip("（）") or "全部已确认数据"),
        ("涉及单据", f"{confirmed_docs} 张"),
        ("确认记录行数", f"{total_rows} 行"),
        ("总销售额", _fmt_money(sum(g["total"] for g in by_item))),
        ("总销售数量", f"{sum(g['amount'] for g in by_item):g}"),
    ]
    for k, v in rows:
        c = overview.add_row().cells
        c[0].text, c[1].text = k, v

    def _add_table(title: str, groups: list[dict[str, Any]], col: str) -> None:
        if not groups:
            return
        doc.add_heading(title, level=1)
        t = doc.add_table(rows=0, cols=5)
        t.style = "Light Grid Accent 1"
        hdr = t.add_row().cells
        hdr[0].text, hdr[1].text, hdr[2].text, hdr[3].text, hdr[4].text = (
            _LABEL.get(col, col), "数量", "金额", "行数", "占比"
        )
        total_sum = sum(g["total"] for g in groups) or 1
        for g in groups[:15]:
            c = t.add_row().cells
            c[0].text = str(g["group"])
            c[1].text = f"{g['amount']:g}"
            c[2].text = _fmt_money(g["total"])
            c[3].text = str(g["rows"])
            c[4].text = f"{g['total'] / total_sum * 100:.1f}%"

        if len(groups)>15:
            doc.add_paragraph(f'该维度共{len(groups)}组，表中展示前15组，省略{len(groups)-15}组；总览包含全部匹配记录。')

    _add_table("二、按商品统计", by_item, "item")
    _add_table("三、按顾客公司统计", by_desc, "desc")
    _add_table("四、按发注日统计", by_date, "date")

    # 图表嵌入
    if include_chart:
        fig = None
        chart_png = None
        try:
            from tools.plot_tool import _collect_data, _setup_cjk_font

            _setup_cjk_font()
            import matplotlib

            matplotlib.use("Agg")
            import matplotlib.pyplot as plt

            labels, values = _collect_data(group_by, filter_query=keyword, year=year, snapshot=snapshot)
            if labels:
                fig, ax = plt.subplots(figsize=(8, 4.2))
                ax.bar(labels, values, color="#4C78A8")
                ax.set_title(f"金额合计(按{_LABEL.get(group_by, group_by)})")
                ax.tick_params(axis="x", rotation=30)
                fig.tight_layout()
                chart_png = REPORTS_DIR / f"_report_chart_{artifact_id}.png"
                fig.savefig(chart_png, dpi=110)
                plt.close(fig)
                doc.add_heading("五、统计图", level=1)
                doc.add_picture(str(chart_png), width=Pt(420))
                chart_png.unlink(missing_ok=True)
        except Exception:  # noqa: BLE001  图表失败不影响报告主体
            chart_warning = '图表未生成，统计表已保留；请核对绘图条件后重新生成。'
            doc.add_paragraph(chart_warning)
        finally:
            if fig is not None:
                plt.close(fig)
            if chart_png is not None:
                chart_png.unlink(missing_ok=True)

    doc.add_paragraph()
    note = doc.add_paragraph()
    nr = note.add_run("数据来源：DocMind 智能体已人工确认(confirmed)的销售识别记录。")
    nr.font.size = Pt(9)
    nr.font.color.rgb = RGBColor(0x99, 0x99, 0x99)

    # ---- 存盘 ----
    safe_year = re.sub(r"[^\w]", "", year) if year else "all"
    fname = REPORTS_DIR / f"销售报告_{safe_year}_{artifact_id}.docx"
    doc.save(fname)
    from db import artifacts
    from agent.runtime_guard import ToolOutcome
    ident = artifacts.register(fname, 'docx', snapshot)
    return ToolOutcome(f"报告已生成: {fname}（共 {confirmed_docs} 张单据, {total_rows} 行记录）" + (f'。{chart_warning}' if chart_warning else '')+f'\nsnapshot_id={ident}；文件卡片提供预览与下载。',
                       cards=[{'type':'artifact','artifact_id':ident}])
