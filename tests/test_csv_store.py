"""csv_store 单元测试：单文件总账 + 同名覆盖 + 确认状态。"""

import csv
from pathlib import Path

from app.services import csv_store
from app.services.csv_store import (
    ALL_SALES_CSV,
    STATUS_CONFIRMED,
    STATUS_PENDING,
)

ROWS_A = [
    {"desc": "顾客A", "date": "2025年08月07日", "from": "源社", "item": "X",
     "amount": "4", "price": "14781.00", "tax": "0.10", "sum": "59124.00"},
]
ROWS_B = [
    {"desc": "顾客B", "date": "2025-08-07", "from": "源社2", "item": "Y",
     "amount": "2", "price": "100.00", "tax": "0.08", "sum": "200.00"},
]


def _read_all(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def test_single_file_accumulates(out_dir: Path):
    p1 = csv_store.save_result_rows(rows=ROWS_A, file_name="a.png", doc_type="sales")
    assert p1.name == ALL_SALES_CSV
    assert p1.parent == out_dir
    p2 = csv_store.save_result_rows(rows=ROWS_B, file_name="b.png", doc_type="sales")
    assert p1 == p2  # 同一总文件

    rows = _read_all(p1)
    assert len(rows) == 2
    assert {r["源图片文件"] for r in rows} == {"a.png", "b.png"}
    assert all(r["状态"] == STATUS_PENDING for r in rows)  # 新行默认待确认


def test_same_name_overwrites_old_rows(out_dir: Path):
    csv_store.save_result_rows(rows=ROWS_A, file_name="a.png", doc_type="sales")
    # 同一张图再次识别（行数可能变化）
    csv_store.save_result_rows(rows=ROWS_A + ROWS_B, file_name="a.png", doc_type="sales")

    rows = _read_all(csv_store.total_csv_path())
    assert len(rows) == 2  # a.png 的旧行被替换为新 2 行，不再叠加


def test_header_columns(out_dir: Path):
    csv_store.save_result_rows(rows=ROWS_A, file_name="a.png", doc_type="sales")
    with csv_store.total_csv_path().open(encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        header = next(reader)
    assert header == [
        "源图片文件", "识别时间", "状态", "修改人", "修改时间",
        "顾客公司", "发注日", "源公司", "项目", "数量", "单价", "税率", "金额",
    ]


def test_utf8_bom(out_dir: Path):
    csv_store.save_result_rows(rows=ROWS_A, file_name="a.png", doc_type="sales")
    raw = csv_store.total_csv_path().read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")


def test_confirm_source_marks_all_rows(out_dir: Path):
    csv_store.save_result_rows(rows=ROWS_A, file_name="a.png", doc_type="sales")
    csv_store.save_result_rows(rows=ROWS_B, file_name="b.png", doc_type="sales")

    n = csv_store.confirm_source("a.png", "张三")
    assert n == 1

    rows = _read_all(csv_store.total_csv_path())
    a_rows = [r for r in rows if r["源图片文件"] == "a.png"]
    b_rows = [r for r in rows if r["源图片文件"] == "b.png"]
    assert a_rows[0]["状态"] == STATUS_CONFIRMED
    assert a_rows[0]["修改人"] == "张三"
    assert a_rows[0]["修改时间"]  # 非空
    assert b_rows[0]["状态"] == STATUS_PENDING  # 其它图不受影响


def test_confirm_unknown_source_returns_zero(out_dir: Path):
    csv_store.save_result_rows(rows=ROWS_A, file_name="a.png", doc_type="sales")
    assert csv_store.confirm_source("nope.png", "张三") == 0


def test_is_source_confirmed(out_dir: Path):
    csv_store.save_result_rows(rows=ROWS_A, file_name="a.png", doc_type="sales")
    assert csv_store.is_source_confirmed("a.png") is False
    csv_store.confirm_source("a.png", "张三")
    assert csv_store.is_source_confirmed("a.png") is True
    assert csv_store.is_source_confirmed("b.png") is False


def test_list_sources(out_dir: Path):
    csv_store.save_result_rows(rows=ROWS_A, file_name="a.png", doc_type="sales")
    csv_store.save_result_rows(rows=ROWS_A + ROWS_B, file_name="b.png", doc_type="sales")
    csv_store.confirm_source("b.png", "张三")
    items = csv_store.list_sources()
    by_name = {i["source_file"]: i for i in items}
    assert set(by_name) == {"a.png", "b.png"}
    assert by_name["a.png"]["status"] == STATUS_PENDING
    assert by_name["a.png"]["row_count"] == "1"
    assert by_name["b.png"]["status"] == STATUS_CONFIRMED
    assert by_name["b.png"]["row_count"] == "2"
