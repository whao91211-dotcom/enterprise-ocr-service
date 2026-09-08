"""csv_store 单元测试：单文件总账 + 同名覆盖更新。"""

import csv
from pathlib import Path

from app.services import csv_store
from app.services.csv_store import ALL_SALES_CSV

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
    assert header == ["源图片文件", "识别时间", "顾客公司", "发注日", "源公司", "项目", "数量", "单价", "税率", "金额"]


def test_utf8_bom(out_dir: Path):
    csv_store.save_result_rows(rows=ROWS_A, file_name="a.png", doc_type="sales")
    raw = csv_store.total_csv_path().read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
