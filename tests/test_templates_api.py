"""模板管理 API 测试。"""

import pytest


async def test_list_and_get_templates(client):
    r = await client.get("/api/v1/templates")
    assert r.status_code == 200
    codes = {t["code"] for t in r.json()}
    assert "invoice" in codes and "contract" in codes

    r = await client.get("/api/v1/templates/invoice")
    assert r.status_code == 200
    body = r.json()
    assert body["code"] == "invoice"
    assert any(f["key"] == "total" for f in body["field_defs"])


async def test_create_and_duplicate(client):
    body = {
        "code": "voucher",
        "name": "报销单",
        "field_defs": [
            {"key": "no", "label": "单号", "type": "string", "required": True},
            {"key": "total", "label": "金额", "type": "amount", "required": True},
        ],
    }
    r = await client.post("/api/v1/templates", json=body)
    assert r.status_code == 201
    assert r.json()["code"] == "voucher"

    # 重复创建 → 409
    r = await client.post("/api/v1/templates", json=body)
    assert r.status_code == 409

    # 新模板可被上传使用
    with pytest.MonkeyPatch.context():
        import respx as _respx

        from tests.conftest import ocr_response

        router = _respx.mock(assert_all_called=False)
        router.start()
        router.post("http://ocr-mock/v1/chat/completions").mock(
            return_value=ocr_response('{"text":"t","fields":{"no":"1","total":9.9}}')
        )
        try:
            r = await client.post(
                "/api/v1/ocr/upload",
                files={"file": ("b.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 10, "image/png")},
                data={"doc_type": "voucher"},
            )
        finally:
            router.stop()
            router.reset()
        assert r.status_code == 201
        assert r.json()["status"] == "pending_review"


async def test_bad_template_code(client):
    body = {"code": "Bad Code!", "name": "x", "field_defs": []}
    r = await client.post("/api/v1/templates", json=body)
    assert r.status_code == 422


async def test_update_template(client):
    body = {
        "code": "invoice",
        "name": "发票(新)",
        "field_defs": [{"key": "invoice_no", "label": "发票号码", "type": "string", "required": True}],
    }
    r = await client.put("/api/v1/templates/invoice", json=body)
    assert r.status_code == 200
    assert r.json()["name"] == "发票(新)"
    assert len(r.json()["field_defs"]) == 1
