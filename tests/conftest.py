"""测试夹具（简化版）：临时 output 目录 + ASGI 客户端 + mock OCR 路由。

env 必须在导入 app 前设置（conftest 先于测试模块执行）。
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest
import respx as _respx
from httpx import ASGITransport, AsyncClient
from httpx import Response as HResponse

# ---------- env 必须在导入 app 前设置 ----------
_PROJ = Path(__file__).resolve().parent.parent
_TMP = Path(_PROJ) / "data" / "test_tmp"
_TMP.mkdir(parents=True, exist_ok=True)

os.environ.setdefault("OCR_BASE_URL", "http://ocr-mock/v1")
os.environ.setdefault("OCR_MODEL", "test-model")
os.environ.setdefault("OCR_RETRIES", "0")
os.environ.setdefault("OCR_TIMEOUT_SECONDS", "10")
os.environ.setdefault("OUTPUT_DIR", str(_TMP / "out"))

# 合法 PNG 魔数样本（接入校验只看魔数与大小；识别结果由 mock 返回）
PNG_SAMPLE = b"\x89PNG\r\n\x1a\n" + b"\x00" * 40

# 模拟 InternVL 返回的 8 列 CSV（训练契约）
OCR_8COL_CONTENT = (
    "SETソフトウェア株式会社,2024年05月22日,菱洋電子貿易(上海)有限公司,掃除機,1,19121.00,0.10,19121.00\n"
    "SETソフトウェア株式会社,2024年05月22日,菱洋電子貿易(上海)有限公司,電子レンジ,10,2865.00,0.10,28650.00"
)


def ocr_response(content: str = OCR_8COL_CONTENT, status: int = 200):
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


@pytest.fixture
def client():
    from app.main import app

    transport = ASGITransport(app=app)
    c = AsyncClient(transport=transport, base_url="http://testserver")
    yield c
    asyncio.run(c.aclose())


@pytest.fixture
def ocr_route():
    """默认 OCR mock：成功返回 8 列销售 CSV。"""
    router = _respx.mock(assert_all_called=False)
    router.start()
    route = router.post("http://ocr-mock/v1/chat/completions").mock(return_value=ocr_response())
    yield route
    router.stop()
    router.reset()


@pytest.fixture
def out_dir() -> Path:
    d = Path(os.environ["OUTPUT_DIR"])
    d.mkdir(parents=True, exist_ok=True)
    return d


async def upload_image(client, content=PNG_SAMPLE, doc_type="sales", filename="a.png", **extra):
    data = {"doc_type": doc_type, **extra}
    return await client.post(
        "/api/v1/ocr/recognize",
        files={"file": (filename, content, "image/png")},
        data=data,
    )
