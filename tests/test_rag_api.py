"""RAG 出口 API 测试：增量数据、游标分页、墓碑。"""

from tests.conftest import PNG_SAMPLE, upload_image


def _png(n: int) -> bytes:
    """同前缀不同内容的 PNG 字节（避免 sha256 去重干扰）。"""
    return PNG_SAMPLE + bytes([n % 256]) * 8 + str(n).encode()


async def _make_approved(client, n: int = 1, **kw):
    doc = (await upload_image(client, content=_png(n), **kw)).json()
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
                    "amount": 1000.0,
                    "tax": 130.0,
                    "total": 1130.0,
                }
            },
            "comment": "ok",
        },
    )
    assert r.status_code == 200, r.text
    return doc["id"]


async def test_rag_data_returns_approved_only(client, ocr_route):
    await _make_approved(client, n=1, filename="approved.png")
    pending = (await upload_image(client, content=_png(2), filename="pending.png")).json()
    assert pending["status"] == "pending_review"

    r = await client.get("/api/v1/rag/data")
    assert r.status_code == 200
    body = r.json()
    assert len(body["items"]) == 1
    row = body["items"][0]
    assert row["fields"]["total"] == 1130.0
    assert row["version_no"] == 2
    assert body["next_cursor"] is None


async def test_rag_data_cursor_pagination(client, ocr_route):
    ids = [await _make_approved(client, n=i, filename=f"c{i}.png") for i in range(3)]

    collected: list[str] = []
    cursor = None
    while True:
        params = {"page_size": 1}
        if cursor:
            params["cursor"] = cursor
        r = await client.get("/api/v1/rag/data", params=params)
        assert r.status_code == 200, r.text
        body = r.json()
        collected.extend(i["document_id"] for i in body["items"])
        cursor = body["next_cursor"]
        if cursor is None:
            break
    assert sorted(collected) == sorted(ids)  # 无重复无遗漏


async def test_rag_data_since_and_doc_type_filters(client, ocr_route):
    await _make_approved(client, n=1, filename="a.png")
    r = await client.get("/api/v1/rag/data", params={"updated_since": "2999-01-01T00:00:00"})
    assert r.json()["items"] == []

    r2 = await client.get("/api/v1/rag/data", params={"doc_type": "contract"})
    assert r2.json()["items"] == []

    r3 = await client.get("/api/v1/rag/data", params={"updated_since": "bad-date"})
    assert r3.status_code == 422


async def test_rag_tombstone_after_reopen(client, ocr_route):
    doc_id = await _make_approved(client, n=1)
    # 发布后发现问题 → reopen 返修 → 从 rag/data 消失并出现在 tombstone
    r = await client.post(f"/api/v1/documents/{doc_id}/reopen", json={"comment": "返修"})
    assert r.status_code == 200

    data = await client.get("/api/v1/rag/data")
    assert data.json()["items"] == []

    tombstones = await client.get(
        "/api/v1/rag/tombstones", params={"updated_since": "2000-01-01T00:00:00"}
    )
    assert tombstones.status_code == 200
    items = tombstones.json()["items"]
    assert len(items) == 1
    assert items[0]["document_id"] == doc_id
    assert items[0]["status"] == "pending_review"


async def test_rag_reject_never_exposed(client, ocr_route):
    doc = (await upload_image(client, content=_png(9))).json()
    await client.post(
        f"/api/v1/documents/{doc['id']}/review",
        json={"action": "reject", "comment": "坏图"},
    )
    data = await client.get("/api/v1/rag/data")
    assert data.json()["items"] == []
    # 从未 approved 过 → 不进 tombstone（避免噪音）
    ts = await client.get(
        "/api/v1/rag/tombstones", params={"updated_since": "2000-01-01T00:00:00"}
    )
    assert ts.json()["items"] == []
