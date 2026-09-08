"""csv_store 单元测试：落盘平铺 output/ + UTF-8 BOM + meta。"""

import csv
from pathlib import Path

from app.services import csv_store


def test_save_result_csv_creates_file(out_dir: Path):
    rows = [
        {"desc": "顾客A", "date": "2025年08月07日", "from": "源社", "item": "X",
         "amount": "4", "price": "14781.00", "tax": "0.10", "sum": "59124.00"},
    ]
    path = csv_store.save_result_csv(rows=rows, file_name="Sample100.png", doc_type="sales")
    assert path.is_file()
    assert path.parent == out_dir  # 平铺在 output/
    # 文件名含原图名与时间戳
    assert path.name.startswith("Sample100_")
    assert path.suffix == ".csv"


def test_csv_bom_and_headers(out_dir: Path):
    rows = [
        {"desc": "顾客A", "date": "2025年08月07日", "from": "源社", "item": "X",
         "amount": "4", "price": "14781.00", "tax": "0.10", "sum": "59124.00"},
    ]
    path = csv_store.save_result_csv(rows=rows, file_name="a.png", doc_type="sales")
    raw = path.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")  # UTF-8 BOM
    with path.open(encoding="utf-8-sig", newline="") as f:
        lines = list(csv.reader(f))
    assert lines[0] == ["顾客公司", "发注日", "源公司", "项目", "数量", "单价", "税率", "金额"]
    assert lines[1] == ["顾客A", "2025年08月07日", "源社", "X", "4", "14781.00", "0.10", "59124.00"]


def test_meta_json_written(out_dir: Path):
    rows = [{"desc": "a", "date": "2025-01-01", "from": "b", "item": "c",
             "amount": "1", "price": "1.00", "tax": "0", "sum": "1.00"}]
    path = csv_store.save_result_csv(rows=rows, file_name="x.png", doc_type="sales",
                                     meta={"model": "m", "latency_ms": 100})
    meta_path = path.with_suffix(".meta.json")
    assert meta_path.is_file()
    import json

    data = json.loads(meta_path.read_text(encoding="utf-8"))
    assert data["row_count"] == 1
    assert data["source_file"] == "x.png"
    assert data["model"] == "m"
