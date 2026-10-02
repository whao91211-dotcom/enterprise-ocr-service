"""Tool3: 检索统计 —— 关系型数据库(SQLite) SQL 直查, 替代全表读入内存。

已确认(confirmed)数据在 SQLite, 直接 WHERE/GROUP BY/SUM 查询:
  rag_query(query, year, top_k)     按关键词+年份过滤明细
  rag_summarize(group_by, keyword, year)  聚合统计(数量/金额)
关键词/年份在 SQL 层过滤, 只把聚合结果喂给 LLM —— 精确且省 token。
"""

from __future__ import annotations

import json

from langchain_core.tools import tool

from db import crud
from agent.field_semantics import FIELD_MEANINGS, SUMMARY_MEANINGS

_SELECTABLE = {"item", "desc", "date"}
_MAX_DETAILS = 20
_MAX_GROUPS = 20
_DETAIL_COLUMNS = ["id", "doc_id", "file_name", "desc", "date", "from",
                   "item", "amount", "price", "tax", "sum"]
_SOURCE = {"table": "ocr_rows", "status": "confirmed"}


def _serialize(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


@tool
def rag_query(query: str = "", year: str = "", top_k: int = 20) -> str:
    """在已确认销售数据中查明细，返回结构化表格及来源；省略行数有明确标记。

    rows 仅为样本，不可据此计算全部金额。完整统计请使用 rag_summarize。

    Args:
        query: 关键词(可空), 匹配 商品名/顾客公司/源公司/文件名, 如 "掃除機"。
        year: 年份(可空), 如 "2024" 或 "2024年", 匹配发注日的年份。
        top_k: 最多返回条数(默认20，上限20)，更多明细请缩小关键词/年份范围。
    """
    if top_k < 1:
        return "top_k 必须大于 0。"
    rows = crud.search_confirmed(keyword=query or None, year=year or None,
                                 top_k=min(top_k, _MAX_DETAILS))
    if not rows:
        where = []
        if query:
            where.append(f"关键词 '{query}'")
        if year:
            where.append(f"年份 {year}")
        return f"（未检索到匹配的已确认数据{'[' + '、'.join(where) + ']' if where else ''}）"
    matched = crud.confirmed_count(keyword=query or None, year=year or None)
    return _serialize({
        "source": _SOURCE,
        "filters": {"keyword": query, "year": year},
        "matched_rows": matched,
        "returned_rows": len(rows),
        "omitted_rows": matched - len(rows),
        "columns": _DETAIL_COLUMNS,
        "field_meanings": FIELD_MEANINGS,
        "rows": [[row.get(column) for column in _DETAIL_COLUMNS] for row in rows],
        "note": "仅展示明细样本；总额用 rag_summarize，更多明细请缩小查询范围。",
    })


@tool
def rag_summarize(group_by: str = "item", keyword: str = "", year: str = "") -> str:
    """统计已确认销售数据，返回全部匹配行的总额及前20个分组(按金额降序)。

    summary 覆盖全部匹配行；rows 可能省略分组，数量见 omitted_groups。
    total_amount/amount 是数量；total_sum/total 是票面金额；total_rows/rows 是明细行数。

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
    summary = {
        "total_rows": total,
        "total_amount": round(sum(g["amount"] for g in groups), 2),
        "total_sum": round(sum(g["total"] for g in groups), 2),
    }
    selected = groups[:_MAX_GROUPS]
    return _serialize({
        "source": _SOURCE,
        "filters": {"keyword": keyword, "year": year},
        "group_by": group_by,
        "summary": summary,
        "total_groups": len(groups),
        "returned_groups": len(selected),
        "omitted_groups": len(groups) - len(selected),
        "columns": ["group", "amount", "total", "rows"],
        "field_meanings": {"summary": SUMMARY_MEANINGS,
                           "columns": {"group": "分组名称", "amount": "数量合计",
                                       "total": "票面金额合计", "rows": "明细行数"}},
        "rows": [[g["group"], g["amount"], g["total"], g["rows"]] for g in selected],
        "note": "summary 为全部匹配行的总计；分组可能省略，更多分组请缩小过滤范围。",
    })
