"""Tool3: 检索统计 —— 关系型数据库(SQLite) SQL 直查, 替代全表读入内存。

已确认(confirmed)数据在 SQLite, 直接 WHERE/GROUP BY/SUM 查询:
  rag_query(query, year, top_k)     按关键词+年份过滤明细
  rag_summarize(group_by, keyword, year)  聚合统计(数量/金额)
关键词/年份在 SQL 层过滤, 只把聚合结果喂给 LLM —— 精确且省 token。
"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.tools import tool

from db import crud

_SELECTABLE = {"item", "desc", "date"}


def _fmt_row(r: dict[str, Any]) -> str:
    return (
        f"{r.get('desc','')} | {r.get('date','')} | 源:{r.get('from','')} | {r.get('item','')}×"
        f"{r.get('amount','')} | 单价{r.get('price','')} 税{r.get('tax','')} "
        f"金额{r.get('sum','')} (图:{r.get('file_name','')})"
    )


@tool
def rag_query(query: str = "", year: str = "", top_k: int = 20) -> str:
    """在已确认(confirmed)销售数据中按关键词和/或年份查明细。

    Args:
        query: 关键词(可空), 匹配 商品名/顾客公司/源公司/文件名, 如 "掃除機"。
        year: 年份(可空), 如 "2024" 或 "2024年", 匹配发注日的年份。
        top_k: 最多返回条数(默认20)。
    """
    rows = crud.search_confirmed(keyword=query or None, year=year or None, top_k=top_k)
    if not rows:
        where = []
        if query:
            where.append(f"关键词 '{query}'")
        if year:
            where.append(f"年份 {year}")
        return f"（未检索到匹配的已确认数据{'[' + '、'.join(where) + ']' if where else ''}）"
    lines = [f"检索到 {len(rows)} 条记录" +
             (f"(关键词:{query}" + (f", 年份:{year}" if year else "") + ")" if query or year else "")]
    for r in rows:
        lines.append("  • " + _fmt_row(r))
    return "\n".join(lines)


@tool
def rag_summarize(group_by: str = "item", keyword: str = "", year: str = "") -> str:
    """统计已确认(confirmed)销售数据的聚合结果(数量合计/金额合计)。

    Args:
        group_by: 分组维度: item(按商品) / desc(按顾客公司) / date(按发注日)。
        keyword: 可选过滤关键词(商品名/公司名), 空=不限制。
        year: 可选年份过滤, 如 "2024" 或 "2024年"。
    """
    if group_by not in _SELECTABLE:
        return f"group_by 仅支持: {sorted(_SELECTABLE)}（收到 {group_by}）"
    groups = crud.summarize_confirmed(
        group_by=group_by, keyword=keyword or None, year=year or None
    )
    total = crud.confirmed_count(keyword=keyword or None, year=year or None)
    if not groups:
        return "（没有符合条件的已确认数据）"
    where = []
    if keyword:
        where.append(f"关键词 '{keyword}'")
    if year:
        where.append(f"年份 {year}")
    head = f"统计(按 {group_by}{('，'+'、'.join(where)) if where else ''}, {total} 条确认行):"
    lines = [head]
    for g in groups:
        lines.append(
            f"  • {g['group']}: 数量 {g['amount']:g}, 金额 {g['total']:.2f}, {g['rows']} 行"
        )
    summary = {
        "group_by": group_by,
        "total_rows": total,
        "total_amount": round(sum(g["amount"] for g in groups), 2),
        "total_sum": round(sum(g["total"] for g in groups), 2),
    }
    lines.append(json.dumps(summary, ensure_ascii=False))
    return "\n".join(lines)
