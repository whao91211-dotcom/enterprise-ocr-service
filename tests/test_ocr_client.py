"""OCR 客户端单元测试：重试策略 / 响应结构错误 / 配置缺失。"""

import os

import pytest
from httpx import Response as HResponse

from app.core.config import get_settings
from app.services import ocr_client
from app.services.ocr_client import OcrCallError, OcrConfigError

URL = "http://ocr-mock/v1/chat/completions"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 40


async def _settings(**overrides):
    """基于 env 重建 settings 快照（不污染全局缓存）。"""
    old = dict(os.environ)
    try:
        for k, v in overrides.items():
            os.environ[k] = str(v)
        get_settings.cache_clear()
        s = get_settings()
        return s
    finally:
        os.environ.clear()
        os.environ.update(old)
        get_settings.cache_clear()


async def _call(s: object):
    return await ocr_client.call_ocr(s, PNG, "image/png", "识别")  # type: ignore[arg-type]


async def test_success_once_no_retry():
    import respx as _respx

    settings = await _settings(OCR_RETRIES=0)
    with _respx.mock:
        route = _respx.post(URL).mock(
            side_effect=lambda req: HResponse(200, json={"choices": [{"message": {"content": "x"}}]})
        )
        result = await _call(settings)
        assert result.content == "x"
        assert route.call_count == 1


async def test_retries_on_5xx_then_success():
    import respx as _respx

    settings = await _settings(OCR_RETRIES=2)
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        if calls["n"] < 3:
            return HResponse(status_code=500)
        return HResponse(200, json={"choices": [{"message": {"content": '{"text":"ok"}'}}]})

    with _respx.mock:
        route = _respx.post(URL).mock(side_effect=handler)
        result = await _call(settings)
        assert result.content == '{"text":"ok"}'
        assert calls["n"] == 3
        assert route.call_count == 3


async def test_gives_up_after_retries():
    import respx as _respx

    settings = await _settings(OCR_RETRIES=2)
    calls = {"n": 0}
    with _respx.mock:
        _respx.post(URL).mock(
            side_effect=lambda req: (_calls_inc(calls), HResponse(status_code=500))[1]
        )
        with pytest.raises(OcrCallError) as ei:
            await _call(settings)
        assert calls["n"] == 3  # 1 + 2 次重试
        assert ei.value.status_code == 500


async def test_missing_content_field_is_error():
    import respx as _respx

    settings = await _settings(OCR_RETRIES=0)
    with _respx.mock:
        _respx.post(URL).mock(
            return_value=HResponse(200, json={"choices": [{"message": {}}]})
        )
        with pytest.raises(OcrCallError):
            await _call(settings)


async def test_model_unset_raises_config_error():
    settings = await _settings(OCR_MODEL="")
    with pytest.raises(OcrConfigError):
        await _call(settings)


def _calls_inc(calls: dict) -> None:
    calls["n"] += 1
