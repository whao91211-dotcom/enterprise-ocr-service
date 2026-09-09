"""共享配置：从项目根 .env / 环境变量读取。

字段:
  OCR_BASE_URL      InternVL OpenAI 兼容端点(默认 http://127.0.0.1:9052/v1)
  OCR_MODEL         模型名(默认 internvl3)
  OCR_API_KEY       模型端点鉴权(通常空)
  OCR_TIMEOUT_SECONDS  识别超时(单槽 llama.cpp 一张约 20~60s, 给足 300)
  OCR_MAX_TOKENS    识别输出上限(1024 完整)
  OCR_TEMPERATURE   0.7
  OCR_TOP_P         0.8
  DEEPSEEK_API_KEY  DeepSeek key(必填, 用于 Agent 问答)
"""

from __future__ import annotations

import os
from pathlib import Path

_PROJ = Path(__file__).resolve().parent  # config.py 所在目录 = 项目根


def _load_env() -> None:
    """把 .env 简单解析进 os.environ(不覆盖已有环境变量)。utf-8-sig 去 BOM。"""
    env_path = _PROJ / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


_load_env()


def _get(key: str, default: str) -> str:
    return os.environ.get(key, "").strip() or default


# ---- OCR (InternVL) ----
OCR_BASE_URL = _get("OCR_BASE_URL", "http://127.0.0.1:9052/v1")
OCR_MODEL = _get("OCR_MODEL", "internvl3")
OCR_API_KEY = _get("OCR_API_KEY", "")
OCR_TIMEOUT_SECONDS = float(_get("OCR_TIMEOUT_SECONDS", "300"))
OCR_MAX_TOKENS = int(_get("OCR_MAX_TOKENS", "1024"))
OCR_TEMPERATURE = float(_get("OCR_TEMPERATURE", "0.7"))
OCR_TOP_P = float(_get("OCR_TOP_P", "0.8"))

# ---- Agent LLM (DeepSeek) ----
DEEPSEEK_API_KEY = _get("DEEPSEEK_API_KEY", "")
DEEPSEEK_MODEL = _get("DEEPSEEK_MODEL", "deepseek-chat")


def require_deepseek_key() -> str:
    if not DEEPSEEK_API_KEY:
        raise RuntimeError(
            "DEEPSEEK_API_KEY 未配置：请在项目根 .env 填入(platform.deepseek.com 申请)"
        )
    return DEEPSEEK_API_KEY
