"""DeepSeek 对话适配器（纯标准库 urllib，无需 openai SDK）。

读取 .env / 环境变量 DEEPSEEK_API_KEY（示例: sk-xxx）。
提供:
  chat(messages)                          -> str  (普通对话)
  chat_with_tools(messages, tools, tool_call) -> (reply_text, tool_calls)
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
MODEL = "deepseek-chat"


def get_api_key() -> str:
    """从 .env 读取 DEEPSEEK_API_KEY（简易解析，不引入 python-dotenv）。"""
    # 1) 环境变量优先
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if key:
        return key
    # 2) 项目根 .env
    env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    if os.path.exists(env_path):
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("DEEPSEEK_API_KEY="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


class DeepSeekError(Exception):
    pass


def _post(payload: dict[str, Any], timeout: float = 60.0) -> dict[str, Any]:
    key = get_api_key()
    if not key:
        raise DeepSeekError("DEEPSEEK_API_KEY 未配置（请在项目根 .env 设置）")
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        DEEPSEEK_URL,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        raise DeepSeekError(f"DeepSeek HTTP {e.code}: {detail}") from e
    except urllib.error.URLError as e:
        raise DeepSeekError(f"DeepSeek 网络错误: {e.reason}") from e


def chat(
    messages: list[dict[str, Any]],
    *,
    model: str = MODEL,
    temperature: float = 0.3,
    max_tokens: int = 1024,
) -> str:
    """普通对话，返回 assistant 文本。"""
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    data = _post(payload)
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as e:
        raise DeepSeekError(f"DeepSeek 响应结构异常: {data}") from e


def chat_with_tools(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    *,
    model: str = MODEL,
    temperature: float = 0.3,
    max_tokens: int = 1024,
) -> tuple[str, list[dict[str, Any]]]:
    """带 function calling 的对话：模型可要求调用工具。

    返回 (assistant_text, tool_calls)。tool_calls 元素形如:
      {"id": "...", "function": {"name": "...", "arguments": "<json串>"}}
    """
    payload = {
        "model": model,
        "messages": messages,
        "tools": tools,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    data = _post(payload)
    try:
        msg = data["choices"][0]["message"]
    except (KeyError, IndexError) as e:
        raise DeepSeekError(f"DeepSeek 响应结构异常: {data}") from e
    text = msg.get("content") or ""
    calls = msg.get("tool_calls") or []
    return text, calls
