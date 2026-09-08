"""已确认数据源：读取 all_sales.csv 的「已确认」行 → 文本化文档列表。

列结构(与 csv_store.HEADERS 对应):
  源图片文件, 识别时间, 状态, 修改人, 修改时间, 顾客公司, 发注日, 源公司, 项目, 数量, 单价, 税率, 金额
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from app.services.csv_store import total_csv_path

STATUS_CONFIRMED = "已确认"

# 数据列的中文表头（按 csv_store.HEADERS 的稳定顺序）
_FIELD_LABELS: list[tuple[str, str]] = [
    ("desc", "顾客公司"),
    ("date", "发注日"),
    ("from", "源公司"),
    ("item", "项目"),
    ("amount", "数量"),
    ("price", "单价"),
    ("tax", "税率"),
    ("sum", "金额"),
]


def _locate_csv(explicit: Path | None) -> Path:
    if explicit is not None:
        return explicit
    return total_csv_path()


def row_to_text(meta_source: str, meta_date: str, cells: dict[str, str]) -> str:
    """把一行已确认数据拼成自然语言文本，便于检索与生成引用。"""
    parts: list[str] = []
    for key, label in _FIELD_LABELS:
        val = cells.get(key, "")
        if val:
            parts.append(f"{label}{val}")
    text = "，".join(parts) if parts else ""
    # 来源信息作尾注，让检索也能命中"文件/时间"类提问
    suffix = f"（源图{meta_source}，识别于{meta_date}）" if meta_source else ""
    return text + suffix


def load_confirmed_docs(csv_path: Path | None = None) -> list[dict[str, Any]]:
    """读取 CSV 中状态=已确认 的行 → [{"id", "source_file", "text", "cells", "meta"}]。

    id 用稳定串: {source_file}::{行号}，便于去重与溯源。
    """
    path = _locate_csv(csv_path)
    if not path.exists():
        return []
    docs: list[dict[str, Any]] = []
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            return []
        # 表头中文 → 内部 key（与 csv_store.HEADERS 对齐；未知列原样保留）
        for row_no, line in enumerate(reader, start=1):
            if line.get("状态") != STATUS_CONFIRMED:
                continue
            source = line.get("源图片文件") or ""
            recog = line.get("识别时间") or ""
            cells: dict[str, str] = {}
            for key, label in _FIELD_LABELS:
                cells[key] = (line.get(label) or "").strip()
            if not any(cells.values()):
                continue
            text = row_to_text(source, recog, cells)
            docs.append(
                {
                    "id": f"{source}::{row_no}",
                    "source_file": source,
                    "recognized_at": recog,
                    "text": text,
                    "cells": cells,
                }
            )
    return docs
