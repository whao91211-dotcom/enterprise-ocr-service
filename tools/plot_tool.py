"""Tool4: Plot —— 把已确认统计数据画成图表(PNG 保存到 charts/)。

供 Agent 在用户要"画图/统计图"时调用, 返回图表文件路径供前端展示。
图表类型: bar(柱状, 默认) / pie(饼图)。
中文字体: 优先尝试系统中文字体(SimHei/Microsoft YaHei), 找不到退回默认。
"""

from __future__ import annotations

from pathlib import Path

import matplotlib
from langchain_core.tools import tool
from matplotlib import font_manager

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from db import crud  # noqa: E402

CHARTS_DIR = Path(__file__).resolve().parents[1] / "charts"


def _setup_cjk_font() -> None:
    candidates = ["SimHei", "Microsoft YaHei", "PingFang SC", "Noto Sans CJK SC",
                  "WenQuanYi Zen Hei", "DejaVu Sans"]
    installed = {f.name for f in font_manager.fontManager.ttflist}
    for name in candidates:
        if name in installed:
            plt.rcParams["font.sans-serif"] = [name]
            break
    plt.rcParams["axes.unicode_minus"] = False


def _collect_data(group_by: str, filter_query: str = "", year: str = "") -> tuple[list[str], list[float]]:
    """用 SQL 聚合取数(替代全表读入内存)。返回 (标签, 金额合计)。"""
    groups = crud.summarize_confirmed(
        group_by=group_by, keyword=filter_query or None, year=year or None
    )
    return [g["group"] for g in groups], [g["total"] for g in groups]


@tool
def plot_chart(group_by: str = "item", chart_type: str = "bar",
               filter_query: str = "", year: str = "") -> str:
    """把已确认销售数据绘制成统计图并保存为 PNG。

    Args:
        group_by: 分组维度: item(按商品) / desc(按顾客公司) / date(按发注日)。
        chart_type: bar(柱状图) 或 pie(饼图)。
        filter_query: 可选过滤关键词(商品名/公司名), 空=全部数据。
        year: 可选年份过滤, 如 "2024" 或 "2024年"。
    """
    _setup_cjk_font()
    labels, values = _collect_data(group_by, filter_query, year)
    if not labels:
        return "（没有可绘图的已确认数据）"

    CHARTS_DIR.mkdir(parents=True, exist_ok=True)
    # 简单 slug 化文件名
    safe = (group_by + (f"_{filter_query}" if filter_query else "") +
            (f"_{year}" if year else "")).replace(" ", "_")
    import re

    safe = re.sub(r"[\\/:*?\"<>|]", "", safe)[:40] or "chart"
    out = CHARTS_DIR / f"{safe}.png"

    fig, ax = plt.subplots(figsize=(8, 5))
    if chart_type == "pie":
        ax.pie(values, labels=labels, autopct="%1.1f%%", startangle=90)
        ax.set_title(f"金额分布(按{group_by})")
    else:
        ax.bar(labels, values, color="#4C78A8")
        ax.set_title(f"金额合计(按{group_by})")
        ax.set_ylabel("金额")
        ax.tick_params(axis="x", rotation=30)
    fig.tight_layout()
    fig.savefig(out, dpi=110)
    plt.close(fig)
    return f"图表已生成: {out} (共 {len(labels)} 类)"
