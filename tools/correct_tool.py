"""Tool2: Correct —— 人工核对/修改识别结果。

因为 OCR 模型准确率约 80%, Agent 需要准确数据, 故提供:
  - correct_list_docs(): 列出所有图及其行数与确认状态
  - correct_show_rows(file_name): 查看某图识别行
  - correct_update_row(row_id, fields): 修改某行字段(人工)
  - correct_confirm(file_name, reviewer): 确认某图全部行(供 RAG 消费)

Web 界面将基于这些能力提供可视化编辑；Agent 也可直接调用。
"""

from __future__ import annotations

from langchain_core.tools import tool

from db import crud

ALLOWED_FIELDS = set(crud.USER_FIELDS)


@tool
def correct_list_docs() -> str:
    """列出数据库中的所有单据(源文件名, 行数, 确认状态), 供人工核对进度。"""
    conn_docs = crud.all_docs_with_stats()
    if not conn_docs:
        return "（数据库暂无单据）"
    lines = ["源文件 | 行数 | 已确认 | 状态"]
    for d in conn_docs:
        lines.append(
            f"{d['file_name']} | {d['row_count']} | {d['confirmed_count']} | "
            f"{'全部已确认' if d['confirmed_count'] == d['row_count'] and d['row_count'] > 0 else '有待确认'}"
        )
    return "\n".join(lines)


@tool
def correct_show_rows(file_name: str) -> str:
    """查看某张图(按源文件名)的全部识别行及每行 id, 供人工核对/修改。

    Args:
        file_name: 源图片文件名(如 Sample100.png)。
    """
    doc = crud.get_document_by_name(file_name)
    if not doc:
        return f"（未找到单据: {file_name}）"
    rows = crud.rows_by_doc(doc["id"])
    if not rows:
        return f"（{file_name} 尚无识别行）"
    lines = [f"单据 {file_name} 共 {len(rows)} 行:"]
    for r in rows:
        status = "已确认" if r["status"] == "confirmed" else "待确认"
        desc = f"{r['desc']} 于{r['date']} 从{r['from']} 购 {r['item']}×{r['amount']} "
        price = f"单价{r['price']} 税{r['tax']} 金额{r['sum']}"
        lines.append(f"  [#{r['id']} {status}] {desc}{price}")
    return "\n".join(lines)


@tool
def correct_update_row(row_id: int, fields: str) -> str:
    """修改某一行识别的字段值(人工修正 OCR 错误)。

    Args:
        row_id: 行 id(correct_show_rows 结果中的 #id)。
        fields: JSON 对象字符串, 键∈desc,date,from,item,amount,price,tax,sum,
                如 '{"item": "掃除機", "price": "19121.00"}'。
    """
    import json

    try:
        data = json.loads(fields)
    except json.JSONDecodeError as e:
        return f"fields 不是合法 JSON: {e}"
    if not isinstance(data, dict):
        return "fields 须为 JSON 对象"
    bad = set(data) - ALLOWED_FIELDS
    if bad:
        return f"不支持的字段: {sorted(bad)}（允许: {sorted(ALLOWED_FIELDS)}）"
    if not crud.update_row(row_id, data, reviewer="manual"):
        return f"行 #{row_id} 更新失败(行可能不存在)"
    return f"✅ 行 #{row_id} 已更新: {data}"


@tool
def correct_confirm(file_name: str, reviewer: str = "manual") -> str:
    """确认某张图的全部识别行(人工核对无误后), 确认后的数据才能进入 RAG/统计。

    Args:
        file_name: 源图片文件名。
        reviewer: 确认人标识。
    """
    doc = crud.get_document_by_name(file_name)
    if not doc:
        return f"（未找到单据: {file_name}）"
    n = crud.confirm_doc(doc["id"], reviewer)
    if n == 0:
        return f"（{file_name} 没有待确认的行）"
    return f"✅ {file_name} 的 {n} 行已确认(操作人: {reviewer})"
