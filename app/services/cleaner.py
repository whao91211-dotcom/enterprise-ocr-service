"""销售单据 8 列清洗：把 OCR 原始单元格规整为用户确认的标签格式。

列顺序与标签(固定):
  desc    顾客公司   —— 文本
  date    发注日     —— YYYY年MM月DD日 归一(如 2025年08月07日)
  from    源公司     —— 文本
  item    项目       —— 文本
  amount  数量       —— 取整(如 4)
  price   单价       —— 去 ¥/千分位逗号，两位小数(如 14781.00)
  tax     税率       —— 原样保留(如 0.10 / 10%)
  sum     金额       —— 去 ¥/千分位逗号，两位小数(如 14781.00)

返回 dict 保持该顺序；无法解析时保留原始字符串并在 warnings 记录。
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any

# 列 key 顺序（与训练标注 / 用户确认的标签顺序一致）
SALES_KEYS: list[str] = ["desc", "date", "from", "item", "amount", "price", "tax", "sum"]

_MONEY_JUNK = re.compile(r"[¥￥\s,，]")
_MONEY_ONLY = re.compile(r"[^\d.\-+]")
_AMOUNT_ONLY = re.compile(r"[^\d.\-+]")


def _to_decimal(raw: Any) -> Decimal | None:
    """把 '¥1,234.50' / '1.234,50'? / '14781' 等转 Decimal；失败返回 None。"""
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    text = _MONEY_JUNK.sub("", text)
    # 千分位在欧洲写法可能是小数点，这里按训练数据仅处理 . 为小数点
    text = _MONEY_ONLY.sub("", text)
    if not text:
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def clean_date(raw: Any) -> str | None:
    """归一日期为 YYYY年MM月DD日；容忍 2025-08-07 / 20250807 / 2025/8/7 / 8月7日?。

    注: 模型输出多为 2024年05月22日 / 2025-08-07。缺年/月时无法归一则返回原串。
    """
    if raw is None:
        return None
    text = str(raw).strip().replace("／", "/")
    if not text:
        return None
    m = re.search(r"(\d{4})\s*[年./\-]\s*(\d{1,2})\s*[月./\-]\s*(\d{1,2})", text)
    if m:
        y, mo, d = (int(g) for g in m.groups())
        return f"{y:04d}年{mo:02d}月{d:02d}日"
    m8 = re.fullmatch(r"(\d{4})(\d{2})(\d{2})", text)
    if m8:
        y, mo, d = (int(g) for g in m8.groups())
        return f"{y:04d}年{mo:02d}月{d:02d}日"
    return text  # 无法归一，保留原文（调用方记 warning）


def clean_money(raw: Any) -> str | None:
    """金额: 去 ¥ 与千分位逗号，保留两位小数字符串(如 '14781.00')。"""
    d = _to_decimal(raw)
    if d is None:
        return None
    return f"{d.quantize(Decimal('0.01')):.2f}"


def clean_amount(raw: Any) -> str | None:
    """数量: 取整(四舍五入到整数)后转字符串(如 '4')。"""
    d = _to_decimal(raw)
    if d is None:
        return None
    return str(int(d.quantize(Decimal("1"), rounding="ROUND_HALF_UP")))


def clean_tax(raw: Any) -> str | None:
    """税率: 原样保留(如 '0.10' / '10%')，只去首尾空白。"""
    if raw is None:
        return None
    text = str(raw).strip()
    return text or None


def _clean_text(raw: Any) -> str | None:
    if raw is None:
        return None
    text = str(raw).strip()
    return text or None


_CLEANERS: dict[str, Any] = {
    "desc": _clean_text,
    "date": clean_date,
    "from": _clean_text,
    "item": _clean_text,
    "amount": clean_amount,
    "price": clean_money,
    "tax": clean_tax,
    "sum": clean_money,
}


def clean_sales_row(raw_row: dict[str, Any] | list[str] | tuple[str, ...]) -> tuple[dict[str, str], list[str]]:
    """清洗一行。raw_row 可为 dict(带 key) 或按 SALES_KEYS 顺序的 list。

    返回 (cleaned: 固定顺序 dict, warnings: list[str])。
    cleaned 值均为字符串（CSV 友好；空字段为 ""）。
    """
    if isinstance(raw_row, dict):
        cells = [raw_row.get(k) for k in SALES_KEYS]
    else:
        cells = list(raw_row) + [None] * (len(SALES_KEYS) - len(raw_row))
        cells = cells[: len(SALES_KEYS)]

    cleaned: dict[str, str] = {}
    warnings: list[str] = []
    for key, raw in zip(SALES_KEYS, cells, strict=True):
        raw = None if raw is None or (isinstance(raw, str) and not raw.strip()) else raw
        val = _CLEANERS[key](raw)
        if val is None:
            cleaned[key] = ""
            if raw is not None and key in ("date", "amount", "price", "sum"):
                warnings.append(f"{key}: 无法解析为规范格式, 原值='{raw}'")
        else:
            cleaned[key] = val
    return cleaned, warnings


def clean_sales_rows(rows: list[Any]) -> tuple[list[dict[str, str]], list[str]]:
    """清洗整表(逐行)。过滤全空行。返回 (cleaned_rows, warnings)。"""
    cleaned_rows: list[dict[str, str]] = []
    warnings: list[str] = []
    for i, row in enumerate(rows, start=1):
        cleaned, w = clean_sales_row(row)
        if not any(cleaned.values()):
            continue  # 全空行丢弃
        cleaned_rows.append(cleaned)
        warnings.extend(f"第{i}行: {msg}" for msg in w)
    return cleaned_rows, warnings
