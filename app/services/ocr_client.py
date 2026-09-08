"""OCR 客户端：调用 OpenAI 兼容的视觉模型端点（本地 9052 隧道转发）。

协议: POST {base}/chat/completions
messages: [{"role":"user","content":[
    {"type":"text","text": instruction},
    {"type":"image_url","image_url":{"url":"data:image/...;base64,..."}}
]}]
"""

from __future__ import annotations

import base64
import time
from dataclasses import dataclass, field
from typing import Any

import httpx
from tenacity import (
    AsyncRetrying,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from app.core.config import Settings


class OcrConfigError(Exception):
    """配置缺失（如 OCR_MODEL 未设置）。"""


class OcrCallError(Exception):
    """调用/响应协议错误（HTTP 错误、响应结构不符）。"""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, OcrCallError):
        return exc.status_code in (408, 429) or (
            exc.status_code is not None and exc.status_code >= 500
        )
    if isinstance(exc, httpx.HTTPError):
        return True
    return False


@dataclass
class OcrResult:
    model_name: str
    raw_response: dict[str, Any]
    content: str  # choices[0].message.content（原始文本，待 result_parser 解析）
    latency_ms: int


@dataclass
class OcrCallOutcome:
    ok: bool = False
    result: OcrResult | None = None
    error: str | None = None
    raw_response: dict[str, Any] = field(default_factory=dict)
    latency_ms: int = 0


def image_data_url(image_bytes: bytes, content_type: str | None) -> str:
    mime = content_type if content_type and content_type.startswith("image/") else "image/jpeg"
    b64 = base64.b64encode(image_bytes).decode("ascii")
    return f"data:{mime};base64,{b64}"


async def _call_once(
    settings: Settings,
    image_bytes: bytes,
    content_type: str | None,
    instruction: str,
) -> OcrResult:
    """调用 OCR 模型一次（不重试）。"""
    if not settings.ocr_model:
        raise OcrConfigError("OCR_MODEL 未配置，请先探测 /v1/models 后设置")

    url = settings.ocr_base_url.rstrip("/") + "/chat/completions"
    headers = {"Content-Type": "application/json"}
    if settings.ocr_api_key:
        headers["Authorization"] = f"Bearer {settings.ocr_api_key}"

    payload: dict[str, Any] = {
        "model": settings.ocr_model,
        "temperature": settings.ocr_temperature,
        "max_tokens": settings.ocr_max_tokens,
        "messages": [
            {"role": "system", "content": "You are a helpful assistant."},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": instruction},
                    {
                        "type": "image_url",
                        "image_url": {"url": image_data_url(image_bytes, content_type)},
                    },
                ],
            }
        ],
    }
    if settings.ocr_top_p:
        payload["top_p"] = settings.ocr_top_p
    if settings.ocr_json_mode:
        payload["response_format"] = {"type": "json_object"}

    start = time.monotonic()
    timeout = httpx.Timeout(settings.ocr_timeout_seconds, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            resp = await client.post(url, headers=headers, json=payload)
        except httpx.HTTPError as exc:
            latency = int((time.monotonic() - start) * 1000)
            raise OcrCallError(f"调用 OCR 服务失败: {exc}") from exc
        latency = int((time.monotonic() - start) * 1000)

    if resp.status_code != 200:
        detail = resp.text[:500]
        raise OcrCallError(
            f"OCR 服务返回 HTTP {resp.status_code}: {detail}", status_code=resp.status_code
        )

    try:
        data = resp.json()
    except ValueError as exc:
        raise OcrCallError("OCR 服务返回非 JSON") from exc

    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise OcrCallError("OCR 响应缺少 choices[0].message.content") from exc

    if not isinstance(content, str):
        content = str(content)
    return OcrResult(
        model_name=settings.ocr_model,
        raw_response=data,
        content=content,
        latency_ms=latency,
    )


async def call_ocr(
    settings: Settings,
    image_bytes: bytes,
    content_type: str | None,
    instruction: str,
) -> OcrResult:
    """调用 OCR 模型（内置网络/5xx/429 重试；总尝试 = ocr_retries+1）。"""
    async for attempt in AsyncRetrying(
        stop=stop_after_attempt(settings.ocr_retries + 1),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        retry=retry_if_exception(_is_retryable),
        reraise=True,
    ):
        with attempt:
            return await _call_once(settings, image_bytes, content_type, instruction)
    raise AssertionError("不可达")
