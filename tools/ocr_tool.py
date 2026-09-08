"""Tool1: OCR 识别(InternVL)。

LangChain @tool：接收图片路径(由 Web 上传后落盘), 调 9052 InternVL 识别,
把 8 列行写入 SQLite(documents + ocr_rows, 状态 pending), 返回识别结果摘要。
"""

from __future__ import annotations

from pathlib import Path

from langchain_core.tools import tool

from db import crud
from tools.ocr_client import OcrError, recognize

ROW_LABELS = {
    "desc": "顾客公司", "date": "发注日", "from": "源公司", "item": "项目",
    "amount": "数量", "price": "单价", "tax": "税率", "sum": "金额",
}


@tool
def ocr_recognize(image_path: str) -> str:
    """识别一张销售单据/支票图片, 返回结构化 8 列并写入数据库(待人工确认)。

    Args:
        image_path: 图片文件路径(png/jpg/jpeg/webp/bmp)。
    """
    p = Path(image_path)
    if not p.is_file():
        return f"错误: 图片不存在: {image_path}"
    ext = p.suffix.lstrip(".").lower() or "png"
    data = p.read_bytes()
    try:
        result = recognize(data, ext)
    except OcrError as e:
        return f"识别失败: {e}"

    rows = result["rows"]
    if not rows:
        return f"识别到 0 行(模型可能未解析)。原始返回: {result['raw'][:200]}"

    file_name = p.name
    doc_id = crud.upsert_document(file_name, None)
    # 同图重复识别: 覆盖旧行(先删该图全部旧行)
    crud.delete_rows_by_doc(doc_id)
    n = crud.insert_rows(doc_id, rows)

    lines = [f"✅ 识别完成: {n} 行(源图 {file_name}, 状态=待确认, 用时{result['latency_ms']}ms)"]
    for i, r in enumerate(rows, 1):
        cells = "，".join(f"{ROW_LABELS[k]}{r.get(k,'')}" for k in crud.USER_FIELDS if r.get(k))
        lines.append(f"{i}. {cells}")
    return "\n".join(lines)
