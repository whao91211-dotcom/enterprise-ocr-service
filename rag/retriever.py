"""RAG 检索服务：已确认数据 BM25 索引的加载与查询封装。

作为智能体的 Tool 被调用：query(text, top_k) -> 命中的原始行(可溯源)。
索引构建策略：数据量小，每次首次查询时惰性构建；可通过 touch() 主动预热。
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from rag.bm25 import BM25Index
from rag.data_source import load_confirmed_docs

_lock = threading.Lock()
_index: BM25Index | None = None
_docs: list[dict[str, Any]] = []
_csv_path: Path | None = None


def _build(csv_path: Path | None = None) -> None:
    global _index, _docs, _csv_path
    with _lock:
        docs = load_confirmed_docs(csv_path)
        idx = BM25Index()
        idx.build(docs)
        _docs = docs
        _index = idx
        _csv_path = csv_path


def touch(csv_path: Path | None = None) -> int:
    """加载/刷新索引，返回已确认文档数。"""
    _build(csv_path)
    return len(_docs)


def search(query: str, top_k: int = 5, csv_path: Path | None = None) -> list[dict[str, Any]]:
    """检索已确认数据，返回命中行（含 score/rank/cells/source_file/text）。"""
    if _index is None or (csv_path is not None and csv_path != _csv_path):
        touch(csv_path)
    assert _index is not None
    return _index.search(query, top_k=top_k)


def format_hits(hits: list[dict[str, Any]]) -> str:
    """把命中行格式化为给 LLM 的紧凑上下文。"""
    lines: list[str] = []
    for h in hits:
        cells = h.get("cells") or {}
        item = cells.get("item") or ""
        desc = cells.get("desc") or ""
        lines.append(
            f"[{h['rank']}] {h['text']}"
            f"（顾客公司:{desc}, 项目:{item}, 源图:{h.get('source_file')}）"
        )
    return "\n".join(lines) if lines else "（未检索到相关已确认销售数据）"


def stats() -> dict[str, Any]:
    """索引统计（供 health/调试）。"""
    if _index is None:
        return {"loaded": False, "docs": 0}
    return {"loaded": True, "docs": len(_docs)}
