"""多来源接入与批量队列 worker 测试。"""


from httpx import Response as HResponse

from tests.conftest import PNG_SAMPLE, upload_image


async def test_submit_local_path_ok(client, ocr_route, inbox_path):
    f = inbox_path / "scan_001.png"
    f.write_bytes(PNG_SAMPLE)
    r = await client.post(
        "/api/v1/ocr/submit",
        json={"doc_type": "invoice", "source": {"type": "local_path", "ref": str(f)}},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "pending_review"
    assert body["ingest_source"] == "local_path"
    assert body["original_ref"] == str(f)


async def test_submit_local_path_escape_rejected(client, ocr_route):
    r = await client.post(
        "/api/v1/ocr/submit",
        json={
            "doc_type": "invoice",
            "source": {"type": "local_path", "ref": r"C:\Windows\win.ini"},
        },
    )
    assert r.status_code == 400
    assert "允许根目录" in r.json()["detail"]


async def test_submit_url_fetch_ok(client, ocr_route, inbox_path):
    import respx as _respx

    router = _respx.mock(assert_all_called=False)
    router.start()
    try:
        router.get("http://files.example.com/scan.jpg").mock(
            return_value=HResponse(200, content=PNG_SAMPLE)
        )
        r = await client.post(
            "/api/v1/ocr/submit",
            json={
                "doc_type": "invoice",
                "source": {"type": "url", "ref": "http://files.example.com/scan.jpg"},
            },
        )
        assert r.status_code == 201, r.text
        assert r.json()["ingest_source"] == "url"
    finally:
        router.stop()
        router.reset()


async def test_submit_api_with_token(client, ocr_route):
    import respx as _respx

    captured = {}

    def handler(request):
        captured["auth"] = request.headers.get("authorization")
        return HResponse(200, content=PNG_SAMPLE)

    router = _respx.mock(assert_all_called=False)
    router.start()
    try:
        router.get("http://file-module/docs/1.png").mock(side_effect=handler)
        r = await client.post(
            "/api/v1/ocr/submit",
            json={
                "doc_type": "invoice",
                "source": {"type": "api", "ref": "docs/1.png"},
            },
        )
        assert r.status_code == 201, r.text
        assert captured.get("auth") == "Bearer tok"
    finally:
        router.stop()
        router.reset()


async def test_submit_url_host_allowlist_denied(client, ocr_route, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setenv("INGEST_URL_ALLOWLIST", "good.example.com")
    get_settings.cache_clear()
    try:
        r = await client.post(
            "/api/v1/ocr/submit",
            json={
                "doc_type": "invoice",
                "source": {"type": "url", "ref": "http://evil.example.com/scan.jpg"},
            },
        )
        assert r.status_code == 400
        assert "白名单" in r.json()["detail"]
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()


async def test_batch_import_worker_flow(client, ocr_route, inbox_path):
    from app.services.batch_worker import BatchWorker

    good = inbox_path / "batch_ok.png"
    good.write_bytes(PNG_SAMPLE)
    missing = inbox_path / "batch_missing.png"

    r = await client.post(
        "/api/v1/ocr/import",
        json={
            "doc_type": "invoice",
            "items": [
                {"source": "local_path", "ref": str(good)},
                {"source": "local_path", "ref": str(missing)},
            ],
        },
    )
    assert r.status_code == 202, r.text
    batch_id = r.json()["batch_id"]
    assert r.json()["accepted"] == 2

    # 驱动 worker 处理一轮
    worker = BatchWorker(poll_interval=999, page_size=5)
    await worker._process_page()

    prog = await client.get(f"/api/v1/ocr/import/{batch_id}")
    assert prog.status_code == 200
    body = prog.json()
    assert body["total"] == 2
    assert body["done"] == 1, body
    assert body["failed"] == 1, body
    failed_item = next(i for i in body["items"] if i["status"] == "failed")
    assert "不存在" in (failed_item["error"] or "")

    # 修复文件后重试失败项 → 成功
    missing.write_bytes(PNG_SAMPLE)
    rr = await client.post(f"/api/v1/queue/{failed_item['id']}/retry")
    assert rr.status_code == 200
    await worker._process_page()

    prog2 = await client.get(f"/api/v1/ocr/import/{batch_id}")
    body2 = prog2.json()
    assert body2["done"] == 2, body2
    # 两条都产生文档
    done_ids = [i["document_id"] for i in body2["items"] if i["status"] == "done"]
    assert all(done_ids)


async def test_import_unknown_template_rejected(client, ocr_route):
    r = await client.post(
        "/api/v1/ocr/import",
        json={"doc_type": "nope", "items": [{"source": "local_path", "ref": "x.png"}]},
    )
    assert r.status_code == 422


async def test_submit_batch_duplicate_png_is_idempotent(client, ocr_route, inbox_path):
    """同一 sha256 经不同来源提交 → 同一文档，不重复 OCR。"""
    f = inbox_path / "dup.png"
    f.write_bytes(PNG_SAMPLE)

    r1 = await client.post(
        "/api/v1/ocr/submit",
        json={"doc_type": "invoice", "source": {"type": "local_path", "ref": str(f)}},
    )
    r2 = await upload_image(client, filename="dup_upload.png")
    assert r1.status_code == 201 and r2.status_code == 201
    assert r1.json()["id"] == r2.json()["id"]
