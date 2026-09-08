"""识别结果落盘：每张图一个 .csv 文档，平铺在 output/ 目录。

文件命名: {原图名(去扩展)}_YYYYmmdd_HHMMSS.csv，同秒冲突加序号。
内容: UTF-8 with BOM（Excel 直接打开不乱码）；首行表头按 8 列中文标签，
其余行为清洗后的数据。另写同名 .meta.json 记录原图/识别时间/模型等信息。
"""

from __future__ import annotations

import csv
import json
import os
import re
from datetime import datetime
from pathlib import Path

from app.core.config import get_settings
from app.services.cleaner import SALES_KEYS

# 表头: key -> 中文标签（与用户确认的标签一致）
HEADERS: dict[str, str] = {
    "desc": "顾客公司",
    "date": "发注日",
    "from": "源公司",
    "item": "项目",
    "amount": "数量",
    "price": "单价",
    "tax": "税率",
    "sum": "金额",
}


def _sanitize_stem(file_name: str | None) -> str:
    name = os.path.basename(file_name or "image")
    stem = re.sub(r"(?i)\.(png|jpe?g|webp|bmp|gif)$", "", name)
    stem = re.sub(r'[\\/:*?"<>|\s]+', "_", stem).strip("_") or "image"
    return stem


def _output_root() -> Path:
    root = Path(get_settings().output_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _unique_path(root: Path, stem: str) -> Path:
    base = root / f"{stem}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    path = base.with_suffix(".csv")
    n = 1
    while path.exists():
        path = root / f"{stem}_{n}.csv"
        n += 1
    return path


def save_result_csv(
    *,
    rows: list[dict],
    file_name: str | None,
    doc_type: str,
    meta: dict | None = None,
) -> Path:
    """清洗后的行落盘为 CSV。返回文件路径。

    rows: list[dict]，键为 SALES_KEYS 顺序中的 key（允许缺失/多余，写盘按固定列序）。
    """
    root = _output_root()
    path = _unique_path(root, _sanitize_stem(file_name))
    headers = [HEADERS[k] for k in SALES_KEYS]

    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        for row in rows:
            writer.writerow([str(row.get(k, "")) for k in SALES_KEYS])

    # 同名的 .meta.json（给 RAG/人工溯源用）
    meta_path = path.with_suffix(".meta.json")
    meta_data = {
        "doc_type": doc_type,
        "source_file": file_name,
        "csv_file": path.name,
        "row_count": len(rows),
        "recognized_at": datetime.now().isoformat(timespec="seconds"),
        "columns": SALES_KEYS,
    }
    if meta:
        meta_data.update(meta)
    meta_path.write_text(
        json.dumps(meta_data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return path
