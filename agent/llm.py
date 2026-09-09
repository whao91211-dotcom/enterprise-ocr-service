"""DeepSeek LLM 封装(langchain-deepseek 官方接入)。"""

from __future__ import annotations

from langchain_deepseek import ChatDeepSeek

import config


def get_llm() -> ChatDeepSeek:
    key = config.require_deepseek_key()
    return ChatDeepSeek(model=config.DEEPSEEK_MODEL, api_key=key, temperature=0.3)
