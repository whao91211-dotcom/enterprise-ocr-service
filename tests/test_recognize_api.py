"""recognize API 集成测试：上传 → mock OCR → 8 列清洗 → 总 csv + 确认保护。"""

import csv
from pathlib import Path

from app.services.csv_store import ALL_SALES_CSV, STATUS_CONFIRMED
from tests.conftest import PNG_SAMPLE, ocr_response, upload_image


async def test_recognize_full_flow(client, ocr_route, out_dir: Path):
    resp = await upload_image(client, content=PNG_SAMPLE, filename="Sample100.png")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["ok"] is True
    assert data["skipped"] is False
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
    assert lines[0] == ["源图片文件", "识别时间", "状态", "修改人", "修改时间",
                        "顾客公司", "发注日", "源公司", "项目", "数量", "单价", "税率", "金额"]
    assert len(lines) == 3  # 表头 + 2 行
    assert lines[1][0] == "Sample100.png"  # 源图名列
    assert lines[1][2] == "待确认"  # 状态列


async def test_two_images_accumulate_same_file(client, ocr_route, out_dir: Path):
    await upload_image(client, filename="a.png")
    await upload_image(client, filename="b.png")
    with (out_dir / ALL_SALES_CSV).open(encoding="utf-8-sig", newline="") as f:
        lines = list(csv.reader(f))
    assert len(lines) == 5  # 表头 + 2 + 2
    srcs = {ln[0] for ln in lines[1:]}
    assert srcs == {"a.png", "b.png"}


async def test_confirm_then_reskip(client, ocr_route, out_dir: Path):
    # 上传 → 确认 → 再上传应 skipped（保护人工修改）
    r1 = await upload_image(client, filename="a.png")
    assert r1.json()["row_count"] == 2

    rc = await client.post("/api/v1/ocr/confirm", json={"source_file": "a.png", "reviewer": "张三"})
    assert rc.status_code == 200, rc.text
    assert rc.json()["confirmed_rows"] == 2

    r2 = await upload_image(client, filename="a.png")
    assert r2.status_code == 200
    body = r2.json()
    assert body["skipped"] is True
    assert body["row_count"] == 0  # 未重新识别

    # CSV 保持确认状态未被覆盖
    with (out_dir / ALL_SALES_CSV).open(encoding="utf-8-sig", newline="") as f:
        lines = list(csv.reader(f))
    assert lines[1][2] == STATUS_CONFIRMED


async def test_force_overwrites_confirmed(client, ocr_route, out_dir: Path):
    await upload_image(client, filename="a.png")
    await client.post("/api/v1/ocr/confirm", json={"source_file": "a.png", "reviewer": "张三"})

    r = await upload_image(client, filename="a.png", force="true")
    assert r.status_code == 200
    body = r.json()
    assert body["skipped"] is False
    assert body["row_count"] == 2

    # force 后状态回到待确认
    with (out_dir / ALL_SALES_CSV).open(encoding="utf-8-sig", newline="") as f:
        lines = list(csv.reader(f))
    assert lines[1][2] == "待确认"


async def test_sources_endpoint(client, ocr_route):
    await upload_image(client, filename="a.png")
    await upload_image(client, filename="b.png")
    await client.post("/api/v1/ocr/confirm", json={"source_file": "a.png", "reviewer": "李四"})

    resp = await client.get("/api/v1/ocr/sources")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_sources"] == 2
    assert body["confirmed"] == 1
    assert body["pending"] == 1
    by_name = {i["source_file"]: i for i in body["items"]}
    assert by_name["a.png"]["status"] == "已确认"
    assert by_name["a.png"]["modified_by"] == "李四"
    assert by_name["b.png"]["status"] == "待确认"


async def test_confirm_unknown_source_404(client, ocr_route):
    resp = await client.post("/api/v1/ocr/confirm", json={"source_file": "nope.png"})
    assert resp.status_code == 404


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
