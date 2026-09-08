"""recognize API 集成测试：上传 → mock OCR → 8 列清洗 → 写入总 csv。"""

import csv
from pathlib import Path

from app.services.csv_store import ALL_SALES_CSV
from tests.conftest import PNG_SAMPLE, ocr_response, upload_image


async def test_recognize_full_flow(client, ocr_route, out_dir: Path):
    resp = await upload_image(client, content=PNG_SAMPLE, filename="Sample100.png")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["ok"] is True
    assert data["doc_type"] == "sales"
    assert data["row_count"] == 2
    assert data["columns"] == ["desc", "date", "from", "item", "amount", "price", "tax", "sum"]
    first = data["rows"][0]
    assert first["desc"] == "SETソフトウェア株式会社"
    assert first["date"] == "2024年05月22日"
    assert first["from"] == "菱洋電子貿易(上海)有限公司"
    assert first["price"] == "19121.00"

    # 落盘到总 csv（路径以 all_sales.csv 结尾）
    csv_path = Path(data["csv_path"])
    assert csv_path.is_file()
    assert csv_path.name == ALL_SALES_CSV
    assert csv_path.parent == out_dir
    with csv_path.open(encoding="utf-8-sig", newline="") as f:
        lines = list(csv.reader(f))
    assert lines[0] == ["源图片文件", "识别时间", "顾客公司", "发注日", "源公司", "项目", "数量", "单价", "税率", "金额"]
    assert len(lines) == 3  # 表头 + 2 行
    assert lines[1][0] == "Sample100.png"  # 源图名列


async def test_two_images_accumulate_same_file(client, ocr_route, out_dir: Path):
    await upload_image(client, filename="a.png")
    await upload_image(client, filename="b.png")
    with (out_dir / ALL_SALES_CSV).open(encoding="utf-8-sig", newline="") as f:
        lines = list(csv.reader(f))
    assert len(lines) == 5  # 表头 + 2 + 2
    srcs = {ln[0] for ln in lines[1:]}
    assert srcs == {"a.png", "b.png"}


async def test_recognize_include_raw(client, ocr_route):
    resp = await upload_image(client, include_raw="true")
    assert resp.status_code == 200
    data = resp.json()
    assert "SETソフトウェア株式会社" in (data["raw"] or "")


async def test_reject_unsupported_doc_type(client, ocr_route):
    resp = await upload_image(client, doc_type="invoice")
    assert resp.status_code == 422
    assert "暂不支持" in resp.json()["detail"]


async def test_reject_bad_bytes(client, ocr_route):
    resp = await upload_image(client, content=b"not-an-image", filename="a.txt")
    assert resp.status_code == 400


async def test_ocr_failure_returns_422(client, ocr_route):
    ocr_route.mock(return_value=ocr_response(status=500))
    resp = await upload_image(client)
    assert resp.status_code == 422


async def test_healthz(client):
    resp = await client.get("/healthz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["ocr_model_configured"] is True
