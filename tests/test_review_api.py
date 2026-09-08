"""审核工作流 API 测试（queue/detail/approve/reject/reopen/history/audit/image）。"""


from tests.conftest import PNG_SAMPLE, upload_image


async def _upload_pending(client):
    r = await upload_image(client)
    assert r.status_code == 201
    return r.json()


async def test_queue_lists_pending_with_preview(client, ocr_route):
    doc = await _upload_pending(client)
    q = await client.get("/api/v1/review/queue")
    assert q.status_code == 200
    body = q.json()
    assert body["total"] == 1
    item = body["items"][0]
    assert item["id"] == doc["id"]
    assert item["preview_text"].startswith("发票号码")
    assert item["preview_fields"]["invoice_no"] == "1100234567"

    # doc_type 过滤
    q2 = await client.get("/api/v1/review/queue", params={"doc_type": "contract"})
    assert q2.json()["total"] == 0


async def test_reject_requires_comment(client, ocr_route):
    doc = await _upload_pending(client)
    r = await client.post(
        f"/api/v1/documents/{doc['id']}/review",
        json={"action": "reject", "expected_version": 1},
    )
    assert r.status_code == 422
    assert "comment" in r.json()["detail"]


async def test_reject_flow(client, ocr_route):
    doc = await _upload_pending(client)
    r = await client.post(
        f"/api/v1/documents/{doc['id']}/review",
        json={"action": "reject", "comment": "图片模糊，请重新扫描"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "rejected"

    detail = await client.get(f"/api/v1/documents/{doc['id']}")
    assert detail.json()["status"] == "rejected"

    audit = await client.get(f"/api/v1/documents/{doc['id']}/audit")
    assert any(a["action"] == "reject" for a in audit.json())
    # 已打回的不出现在待审队列
    q = await client.get("/api/v1/review/queue")
    assert q.json()["total"] == 0


async def test_approve_optimistic_lock(client, ocr_route):
    doc = await _upload_pending(client)
    # 错误的 expected_version → 409
    r = await client.post(
        f"/api/v1/documents/{doc['id']}/review",
        json={"action": "approve", "expected_version": 99},
    )
    assert r.status_code == 409
    assert "期望版本" in r.json()["detail"]


async def test_approve_with_corrections_and_validation(client, ocr_route):
    doc = await _upload_pending(client)
    # 错误金额 → 422 并带字段错误
    r = await client.post(
        f"/api/v1/documents/{doc['id']}/review",
        json={
            "action": "approve",
            "expected_version": 1,
            "corrections": {
                "fields": {
                    "invoice_code": "011002300511",
                    "invoice_no": "1100234567",
                    "invoice_date": "2025-01-08",
                    "seller": "示例贸易有限公司",
                    "seller_tax_no": "91110000MA01XXXXXX",
                    "buyer": "示例采购有限公司",
                    "amount": "不是数字",
                    "tax": 130.0,
                    "total": 1130.0,
                }
            },
        },
    )
    assert r.status_code == 422, r.text
    errs = r.json()["detail"]
    assert any(e["field"] == "amount" for e in errs)
    assert any(e["label"].startswith("金额") for e in errs)

    # 修正后 approve → approved，version=2，diff 留痕
    r2 = await client.post(
        f"/api/v1/documents/{doc['id']}/review",
        json={
            "action": "approve",
            "expected_version": 1,
            "corrections": {
                "fields": {
                    "invoice_code": "011002300511",
                    "invoice_no": "1100234567",
                    "invoice_date": "2025-01-08",
                    "seller": "示例贸易有限公司(修正)",
                    "seller_tax_no": "91110000MA01XXXXXX",
                    "buyer": "示例采购有限公司",
                    "amount": "1234.56",
                    "tax": 130.0,
                    "total": 1130.0,
                },
                "text": "修正后的全文",
            },
            "comment": "人工修正金额与销售方",
        },
    )
    assert r2.status_code == 200, r2.text
    out = r2.json()
    assert out["status"] == "approved"
    assert out["current_version"] == 2
    assert out["new_version"] == 2
    keys = {c["key"] for c in out["field_changes"]}
    assert "seller" in keys and "amount" in keys and "text" not in keys

    hist = await client.get(f"/api/v1/documents/{doc['id']}/history")
    hb = hist.json()
    assert [v["version_no"] for v in hb["versions"]] == [1, 2]
    assert hb["versions"][1]["kind"] == "review"
    assert hb["versions"][1]["editor"] == "local"  # AUTH_MODE=none
    assert hb["versions"][1]["fields"]["seller"] == "示例贸易有限公司(修正)"
    approve_audit = next(a for a in hb["audit"] if a["action"] == "approve")
    assert approve_audit["field_changes"] and approve_audit["comment"] == "人工修正金额与销售方"


async def test_approve_without_corrections_ok(client, ocr_route):
    doc = await _upload_pending(client)
    r = await client.post(
        f"/api/v1/documents/{doc['id']}/review",
        json={"action": "approve", "expected_version": 1, "comment": "无误"},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "approved"
    assert r.json()["field_changes"] == []


async def test_reopen_approved(client, ocr_route):
    doc = await _upload_pending(client)
    await client.post(
        f"/api/v1/documents/{doc['id']}/review",
        json={"action": "approve", "expected_version": 1, "comment": "先通过"},
    )
    r = await client.post(
        f"/api/v1/documents/{doc['id']}/reopen", json={"comment": "发现识别错误，返修"}
    )
    assert r.status_code == 200
    assert r.json()["status"] == "pending_review"
    # 回到待审队列
    q = await client.get("/api/v1/review/queue")
    assert q.json()["total"] == 1


async def test_double_approve_conflict(client, ocr_route):
    doc = await _upload_pending(client)
    await client.post(
        f"/api/v1/documents/{doc['id']}/review",
        json={"action": "approve", "expected_version": 1},
    )
    # 第二个人基于旧版本再 approve → 409
    r = await client.post(
        f"/api/v1/documents/{doc['id']}/review",
        json={"action": "approve", "expected_version": 1},
    )
    assert r.status_code == 409
    assert r.json()["detail"].startswith("文档状态")  # 已 approved


async def test_image_signed_url(client, ocr_route, monkeypatch):
    from app.core.config import get_settings

    doc = await _upload_pending(client)

    # 开启签名：detail 中 image_url 带 expires/sig；无签访问 403；有效签 200
    monkeypatch.setenv("SIGNING_SECRET", "s3cret-test")
    get_settings.cache_clear()
    try:
        detail = await client.get(f"/api/v1/documents/{doc['id']}")
        image_url = detail.json()["image_url"]
        assert "expires=" in image_url and "sig=" in image_url

        r_bad = await client.get(f"/api/v1/documents/{doc['id']}/image")
        assert r_bad.status_code == 403

        r_good = await client.get(image_url)
        assert r_good.status_code == 200
        assert r_good.content == PNG_SAMPLE
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()
