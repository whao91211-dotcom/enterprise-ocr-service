"""rag 子项目测试：BM25 / 数据源 / Tool 注册 / Web 路由。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from rag import retriever
from rag.bm25 import BM25Index
from rag.data_source import load_confirmed_docs


@pytest.fixture(scope="module")
def sample_csv(tmp_path_factory):
    """构造一个小型已确认/待确认混合 CSV。"""
    import csv as _csv

    d = tmp_path_factory.mktemp("rag")
    p = d / "all_sales.csv"
    rows = [
        ["a.png", "2026-01-01T00:00:00", "已确认", "小王", "2026-01-02T00:00:00",
         "甲公司", "2025年08月07日", "乙社", "掃除機", "1", "19121.00", "0.10", "19121.00"],
        ["a.png", "2026-01-01T00:00:00", "已确认", "小王", "2026-01-02T00:00:00",
         "甲公司", "2025年08月07日", "乙社", "電子レンジ", "10", "2865.00", "0.10", "28650.00"],
        ["b.png", "2026-01-03T00:00:00", "待确认", "", "",
         "丙公司", "2025年09月01日", "丁社", "冷蔵庫", "2", "5000.00", "0.00", "10000.00"],
    ]
    with p.open("w", encoding="utf-8-sig", newline="") as f:
        w = _csv.writer(f)
        w.writerow(["源图片文件", "识别时间", "状态", "修改人", "修改时间",
                    "顾客公司", "发注日", "源公司", "项目", "数量", "单价", "税率", "金额"])
        w.writerows(rows)
    return p


def test_tokenize_mixed():
    from rag.bm25 import tokenize

    toks = tokenize("甲公司 2024年 買了掃除機")
    assert "掃除" in toks or "掃" in toks or any("除機" in t for t in toks)
    assert any(t == "2024" for t in toks) or any("年" in t for t in toks)


def test_load_confirmed_only(sample_csv):
    docs = load_confirmed_docs(sample_csv)
    assert len(docs) == 2  # 待确认的 b.png 被过滤
    assert all("待确认" not in d["text"] for d in docs)
    assert docs[0]["source_file"] == "a.png"


def test_bm25_search_ranks(sample_csv):
    docs = load_confirmed_docs(sample_csv)
    idx = BM25Index()
    idx.build(docs)
    hits = idx.search("掃除機", top_k=5)
    assert hits and "掃除機" in hits[0]["text"]
    hits2 = idx.search("冷蔵庫", top_k=5)  # 待确认行不在索引里
    assert not hits2


def test_retriever_roundtrip(sample_csv):
    retriever.touch(sample_csv)
    assert retriever.stats()["docs"] == 2
    hits = retriever.search("電子レンジ", top_k=3, csv_path=sample_csv)
    assert hits and hits[0]["cells"]["item"] == "電子レンジ"


def test_agent_tool_registry():
    from rag.agent import TOOL_REGISTRY

    assert "ocr_recognize" in TOOL_REGISTRY
    assert "rag_query" in TOOL_REGISTRY
    assert "rag_rebuild" in TOOL_REGISTRY


def test_web_routes():
    from rag.web import app

    paths = {r.path for r in app.routes if hasattr(r, "path")}
    assert {"/", "/api/chat", "/api/rebuild", "/api/stats"} <= paths
