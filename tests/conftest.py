"""测试夹具：sqlite 文件库 + 临时目录 + ASGI 客户端 + mock OCR 路由。

env 必须在导入 app 前设置（conftest 先于测试模块执行）。
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path

import pytest
import respx as _respx
from httpx import ASGITransport, AsyncClient
from httpx import Response as HResponse

# ---------- env 必须在导入 app 前设置 ----------
# 测试临时目录放在项目 data/ 下（避免沙箱对系统 Temp 的写限制）
_PROJ = Path(__file__).resolve().parent.parent
_TMP = os.path.join(_PROJ, "data", "test_tmp")
os.makedirs(_TMP, exist_ok=True)
os.environ.setdefault("DATABASE_URL", f"sqlite+aiosqlite:///{_TMP}/test.db")
os.environ.setdefault("STORAGE_DIR", os.path.join(_TMP, "images"))
os.environ.setdefault("OCR_BASE_URL", "http://ocr-mock/v1")
os.environ.setdefault("OCR_MODEL", "test-model")
os.environ.setdefault("OCR_RETRIES", "0")
os.environ.setdefault("AUTH_MODE", "none")
os.environ.setdefault("INGEST_ALLOWED_ROOTS", os.path.join(_TMP, "inbox"))
os.environ.setdefault("INGEST_URL_ALLOWLIST", "*")
os.environ.setdefault("INGEST_API_BASE_URL", "http://file-module")
os.environ.setdefault("INGEST_API_TOKEN", "tok")
os.environ.setdefault("BATCH_POLL_INTERVAL_SECONDS", "0")  # 测试内手动驱动 worker

os.makedirs(os.environ["STORAGE_DIR"], exist_ok=True)
os.makedirs(os.environ["INGEST_ALLOWED_ROOTS"], exist_ok=True)

import app.models as _models  # noqa: E402,F401   # 必须在 create_all 前注册全部表
from app.core.db import Base, dispose_db, get_session_factory, init_db  # noqa: E402

# 合法 PNG 魔数样本（接入校验只看魔数与大小；识别结果由 mock 返回）
PNG_SAMPLE = b"\x89PNG\r\n\x1a\n" + b"\x00" * 40

OCR_OK_CONTENT = (
    '{"text":"发票号码 1100234567\\n销售方：示例贸易有限公司\\n价税合计：1130.00",'
    '"fields":{"invoice_code":"011002300511","invoice_no":"1100234567",'
    '"invoice_date":"2025-01-08","seller":"示例贸易有限公司","seller_tax_no":"91110000MA01XXXXXX",'
    '"buyer":"示例采购有限公司","amount":1000.00,"tax":130.00,"total":1130.00}}'
)


def ocr_response(content: str = OCR_OK_CONTENT, status: int = 200):
    if status != 200:
        return HResponse(status_code=status)
    body = {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "model": "test-model",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }
    return HResponse(status_code=200, json=body)


@pytest.fixture(scope="session", autouse=True)
def _db():
    init_db()
    yield
    asyncio.run(dispose_db())


@pytest.fixture(autouse=True)
def _fresh_tables(_db):
    def _ddl(sync_session):
        bind = sync_session.get_bind()
        Base.metadata.drop_all(bind)
        Base.metadata.create_all(bind)

    async def _reset():
        async with get_session_factory()() as session:
            await session.run_sync(_ddl)
            await session.commit()

    asyncio.run(_reset())
    yield


@pytest.fixture
def client():
    from app.main import app

    transport = ASGITransport(app=app)
    c = AsyncClient(transport=transport, base_url="http://testserver")
    yield c
    asyncio.run(c.aclose())


@pytest.fixture(autouse=True)
async def seed_invoice(_fresh_tables):
    """每测试预置 invoice 模板。"""
    from app.seed import seed_templates

    await seed_templates.seed()


@pytest.fixture
def ocr_route():
    """默认 OCR mock：成功返回发票示例 JSON。"""
    router = _respx.mock(assert_all_called=False)
    router.start()
    route = router.post("http://ocr-mock/v1/chat/completions").mock(return_value=ocr_response())
    yield route
    router.stop()
    router.reset()


@pytest.fixture
def inbox_path() -> Path:
    return Path(os.environ["INGEST_ALLOWED_ROOTS"])


async def upload_image(client, content=PNG_SAMPLE, doc_type="invoice", filename="a.png"):
    return await client.post(
        "/api/v1/ocr/upload",
        files={"file": (filename, content, "image/png")},
        data={"doc_type": doc_type},
    )
