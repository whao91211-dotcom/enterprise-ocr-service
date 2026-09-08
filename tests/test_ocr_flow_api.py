"""端到端 OCR 上传链路测试（mock OCR）。"""


from tests.conftest import PNG_SAMPLE, ocr_response, upload_image

OCR_BAD_JSON = '{"text": "无 fields 只有 text"}'
OCR_EMPTY = ""


async def test_upload_success(client, ocr_route):
    r = await upload_image(client)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "pending_review"
    assert body["current_version"] == 1
    assert body["latest_content"]["kind"] == "ocr"
    assert body["latest_content"]["version_no"] == 1
    # 类型已清洗：金额为 float
    fields = body["latest_content"]["fields"]
    assert fields["total"] == 1130.0
    assert fields["invoice_no"] == "1100234567"
    assert body["latest_ocr"]["status"] == "success"
    assert body["latest_ocr"]["parsed_text"].startswith("发票号码")
    assert body["image_url"].startswith("/api/v1/documents/")
    # 原图可下载（AUTH_MODE=none 且无签名密钥时直放）
    r_img = await client.get(body["image_url"])
    assert r_img.status_code == 200
    assert r_img.content == PNG_SAMPLE


async def test_upload_duplicate_returns_same_document(client, ocr_route):
    r1 = await upload_image(client, filename="a.png")
    r2 = await upload_image(client, filename="b.png")  # 同字节不同文件名
    assert r1.status_code == r2.status_code == 201
    assert r1.json()["id"] == r2.json()["id"]

    # 只有一次 OCR 调用与一个内容版本
    doc_id = r1.json()["id"]
    hist = await client.get(f"/api/v1/documents/{doc_id}/history")
    assert hist.status_code == 200
    body = hist.json()
    assert len(body["versions"]) == 1


async def test_upload_unknown_doc_type(client, ocr_route):
    r = await upload_image(client, doc_type="nope")
    assert r.status_code == 422
    assert "模板不存在" in r.json()["detail"]


async def test_upload_invalid_image_bytes(client, ocr_route):
    r = await upload_image(client, content=b"not an image at all" * 10)
    assert r.status_code == 400
    assert "魔数" in r.json()["detail"] or "无法识别" in r.json()["detail"]


async def test_upload_ocr_fallback_text_only(client, ocr_route):
    # 模型未按契约输出 fields：应降级进队列且带 warnings
    ocr_route.mock(return_value=ocr_response(OCR_BAD_JSON))
    r = await upload_image(client)
    assert r.status_code == 201
    body = r.json()
    assert body["status"] == "pending_review"
    assert body["latest_ocr"]["parse_warnings"]
    assert body["latest_content"]["text"] == "无 fields 只有 text"


async def test_ocr_http_failure_then_retry(client):
    import respx as _respx

    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return ocr_response(status=500)
        return ocr_response()

    router = _respx.mock(assert_all_called=False)
    router.start()
    try:
        router.post("http://ocr-mock/v1/chat/completions").mock(side_effect=handler)
        r = await upload_image(client)
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["status"] == "ocr_failed"
        assert body["error_message"]
        assert body["current_version"] == 0
        doc_id = body["id"]

        # retry → 成功进入待审
        r2 = await client.post(f"/api/v1/documents/{doc_id}/ocr/retry")
        assert r2.status_code == 200, r2.text
        body2 = r2.json()
        assert body2["status"] == "pending_review"
        assert body2["current_version"] == 1
    finally:
        router.stop()
        router.reset()


async def test_ocr_empty_content_fails(client):
    import respx as _respx

    router = _respx.mock(assert_all_called=False)
    router.start()
    try:
        router.post("http://ocr-mock/v1/chat/completions").mock(
            return_value=ocr_response(OCR_EMPTY)
        )
        r = await upload_image(client)
        assert r.status_code == 201
        assert r.json()["status"] == "ocr_failed"
    finally:
        router.stop()
        router.reset()
