"""OCR 模型返回内容解析：容错 JSON 提取 → text + fields。

容忍：markdown 代码块包裹、前后杂文字、text 缺失/空、fields 缺失。
解析失败不抛异常（除完全不可用），降级为带 warnings 的结果 —— 原始返回永远存档。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


@dataclass
class ParsedResult:
    text: str = ""
    fields: dict[str, Any] = field(default_factory=dict)
    rows: list[list[str]] = field(default_factory=list)  # 多行记录（CSV 契约）
    row_mode: bool = False
    warnings: list[str] = field(default_factory=list)
    raw_parsed: dict[str, Any] = field(default_factory=dict)


class ContentParseError(Exception):
    """内容完全不可解析（非 JSON 且无可提取结构）。"""


def _try_load(content: str) -> dict[str, Any] | None:
    text = content.strip()
    if not text:
        return None
    # 1) 直接 json
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass
    # 2) markdown 代码块
    m = _FENCE_RE.search(text)
    if m:
        try:
            obj = json.loads(m.group(1).strip())
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass
    # 3) 截取第一个 { 到最后一个 } 的闭合 JSON 子串
    start, end = text.find("{"), text.rfind("}")
    if 0 <= start < end:
        try:
            obj = json.loads(text[start : end + 1])
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass
    return None


def _looks_like_csv(content: str) -> bool:
    lines = [ln.strip() for ln in content.splitlines() if ln.strip()]
    if len(lines) < 1:
        return False
    # 任意一行有多于 2 个逗号分隔字段，或至少 2 行且含逗号
    return any(ln.count(",") >= 2 for ln in lines) or (
        len(lines) >= 2 and any("," in ln for ln in lines)
    )


def parse_content(content: str) -> ParsedResult:
    """解析模型返回的 content 字符串。

    优先按 JSON（单文档 fields 契约）；否则若形如 CSV 多行则进入 row_mode（多记录契约）。
    """
    obj = _try_load(content)
    result = ParsedResult()
    if obj is None:
        stripped = content.strip()
        if not stripped:
            raise ContentParseError("模型返回内容为空")
        result.text = stripped
        # CSV 多行记录
        if _looks_like_csv(stripped):
            result.row_mode = True
            result.rows = [ln.split(",") for ln in stripped.splitlines() if ln.strip()]
            result.warnings.append("模型返回 CSV 多行记录，已按行解析为结构化记录")
            result.raw_parsed = {"rows": result.rows}
            return result
        result.warnings.append("模型返回非 JSON 且非 CSV，已按全文文本处理")
        result.raw_parsed = {"text": result.text}
        return result

    result.raw_parsed = obj
    text = obj.get("text")
    if isinstance(text, str):
        result.text = text
    else:
        result.warnings.append("返回 JSON 中缺少 text 字段或非字符串")

    fields = obj.get("fields")
    if isinstance(fields, dict):
        result.fields = fields
    else:
        result.warnings.append("返回 JSON 中缺少 fields 对象或非对象")

    # 顶层若直接以字段散列给出（模型未按契约包一层），尝试兜底
    if not result.text and isinstance(obj.get("content"), str):
        result.text = obj["content"]
        result.warnings.append("使用了非契约字段 content 作为全文")
    return result
