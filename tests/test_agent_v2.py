"""agent v2 测试: db CRUD + 数据源(tools) 核心逻辑(不经真实模型/网络)。"""

import os
import sys
import tempfile
from pathlib import Path

import pytest

# 测试用独立 data 目录
_TMP = Path(tempfile.mkdtemp(prefix="agent_v2_test_"))
os.environ["AGENT_DATA_DIR"] = str(_TMP)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from db import crud  # noqa: E402
from db.database import init_db  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_db():
    init_db()
    yield
    for p in _TMP.glob("agent.db*"):
        p.unlink(missing_ok=True)


def _seed(doc_name="t.png", rows=None, confirm=False) -> int:
    rows = rows or [
        {"desc": "甲公司", "date": "2025-08-07", "from": "乙社", "item": "掃除機",
         "amount": "1", "price": "19121.00", "tax": "0.10", "sum": "19121.00"},
        {"desc": "甲公司", "date": "2025-08-07", "from": "乙社", "item": "冷蔵庫",
         "amount": "2", "price": "5000.00", "tax": "0.00", "sum": "10000.00"},
    ]
    doc_id = crud.upsert_document(doc_name, "sha")
    crud.insert_rows(doc_id, rows)
    if confirm:
        crud.confirm_doc(doc_id, "tester")
    return doc_id


def test_insert_and_user_keys():
    doc_id = _seed()
    rows = crud.rows_by_doc(doc_id)
    assert len(rows) == 2
    r = rows[0]
    assert r["desc"] == "甲公司" and r["sum"] == "19121.00"
    assert r["status"] == "pending"
    assert "desc_" not in r  # 用户键无下划线


def test_update_row_user_keys():
    doc_id = _seed()
    rid = crud.rows_by_doc(doc_id)[0]["id"]
    assert crud.update_row(rid, {"item": "電子レンジ", "price": "9999.00"}, "小王")
    r = crud.rows_by_doc(doc_id)[0]
    assert r["item"] == "電子レンジ" and r["price"] == "9999.00"


def test_confirm_and_confirmed_rows_filter():
    _seed(confirm=True)
    docs = crud.all_docs_with_stats()
    assert docs[0]["confirmed_count"] == 2
    cf = crud.confirmed_rows({"item": "冷蔵庫"})
    assert len(cf) == 1 and cf[0]["item"] == "冷蔵庫"


def test_rag_summarize_logic():
    from tools.rag_tool import rag_summarize

    _seed(confirm=True)
    out = rag_summarize.invoke({"group_by": "item"})
    assert "掃除機" in out and "冷蔵庫" in out


def test_rag_query_logic():
    from tools.rag_tool import rag_query

    _seed(confirm=True)
    out = rag_query.invoke({"query": "掃除機", "top_k": 5})
    assert "掃除機" in out
    out2 = rag_query.invoke({"query": "不存在的商品XYZ"})
    assert "未检索到" in out2


def test_correct_tools():
    from tools.correct_tool import (
        correct_confirm,
        correct_list_docs,
        correct_show_rows,
        correct_update_row,
    )

    _seed()
    lst = correct_list_docs.invoke({})
    assert "t.png" in lst
    shown = correct_show_rows.invoke({"file_name": "t.png"})
    assert "掃除機" in shown
    # 提取 id 更新
    import re

    m = re.search(r"\[#(\d+)", shown)
    assert m
    rid = int(m.group(1))
    ok = correct_update_row.invoke({"row_id": rid, "fields": '{"item":"新商品"}'})
    assert "已更新" in ok
    done = correct_confirm.invoke({"file_name": "t.png", "reviewer": "t"})
    assert "已确认" in done
