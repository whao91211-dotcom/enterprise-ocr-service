"""Tool3: RAG —— 已确认数据的检索与统计。

数据库已存人工确认(confirmed)的准确行。此 Tool 让 Agent 能:
  - rag_query(query, top_k): 关键词检索确认行(按商品/顾客公司/日期), 返回明细
  - rag_summarize(group_by, filter): 聚合统计(数量/金额小计), 供"统计/画图"使用
避免把全部行喂给 DeepSeek(省 token、结果准)。
"""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Any

from langchain_core.tools import tool

from db import crud


def _fmt_row(r: dict[str, Any]) -> str:
    return (
        f"{r['desc']} | {r['date']} | 源:{r.get('from','')} | {r['item']}×{r['amount']} "
        f"| 单价{r['price']} 税{r['tax']} 金额{r['sum']} (图:{r.get('file_name','')})"
    )


@tool
def rag_query(query: str, top_k: int = 8) -> str:
    """在已人工确认的销售数据中检索记录。

    Args:
        query: 检索关键词, 支持商品名/公司名/年份等, 如 "掃除機" 或 "甲公司 2024"。
        top_k: 最多返回条数(默认8)。
    """
    # 简单关键词过滤: 将 query 拆词, 命中 item/desc 含任一词的行
    import re

    words = [w for w in re.split(r"[\s,，、]+", query) if w]
    if not words:
        return "（查询为空）"
    rows = crud.confirmed_rows()
    matched: list[dict[str, Any]] = []
    for r in rows:
        hay = " ".join(
            str(r.get(k, "")) for k in ("item", "desc", "date", "from", "file_name")
        ).lower()
        if any(w.lower() in hay for w in words):
            matched.append(r)
    if not matched:
        return "（未检索到匹配的已确认数据）"
    lines = [f"检索到 {len(matched)} 条记录(关键词: {', '.join(words)}):"]
    for r in matched[:top_k]:
        lines.append("  • " + _fmt_row(r))
    if len(matched) > top_k:
        lines.append(f"  … 共 {len(matched)} 条, 仅显示前 {top_k}")
    return "\n".join(lines)


@tool
def rag_summarize(group_by: str = "item", filter_query: str = "") -> str:
    """统计已确认数据的聚合结果(数量合计/金额合计), 供画图或回答汇总问题。

    Args:
        group_by: 分组维度: item(商品) 或 desc(顾客公司) 或 date(发注日)。
        filter_query: 可选过滤关键词(如商品名/公司名), 空=全部。
    """
    rows = crud.confirmed_rows()
    if filter_query:
        fq = filter_query.lower()
        rows = [r for r in rows if fq in " ".join(str(r.get(k, "")) for k in ("item", "desc")).lower()]
    if not rows:
        return "（没有符合条件的已确认数据）"

    key = {"item": "item", "desc": "desc", "date": "date"}.get(group_by, "item")
    groups: dict[str, dict[str, float]] = defaultdict(
        lambda: {"amount": 0.0, "sum": 0.0, "rows": 0}
    )

    def _num(v: str) -> float:
        try:
            return float(str(v).replace(",", "").replace("¥", ""))
        except ValueError:
            return 0.0

    for r in rows:
        g = str(r.get(key, "") or "(空)")
        groups[g]["amount"] += _num(r.get("amount"))
        groups[g]["sum"] += _num(r.get("sum"))
        groups[g]["rows"] += 1

    lines = [f"统计(按 {key}, {len(rows)} 条确认行):"]
    for g, s in sorted(groups.items(), key=lambda kv: -kv[1]["sum"]):
        lines.append(
            f"  • {g}: 数量 {s['amount']:g}, 金额 {s['sum']:.2f}, {s['rows']} 行"
        )
    lines.append(json.dumps(
        {"group_by": key, "total_amount": sum(g["amount"] for g in groups.values()),
         "total_sum": sum(g["sum"] for g in groups.values())}
    ))
    return "\n".join(lines)
