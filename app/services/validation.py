"""字段校验：按单据模板 field_defs 校验/清洗 OCR 字段或人工修正字段。

- OCR 侧(strict=False)：只产生 warnings，不阻断流程；
- 审核侧(strict=True)：errors 非空则拒绝 approve（返回 422 级错误明细）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

_NUMBER_RE = re.compile(r"[^\d.\-+]")


@dataclass
class FieldIssue:
    field: str
    label: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return {"field": self.field, "label": self.label, "message": self.message}


@dataclass
class ValidationOutcome:
    cleaned: Any = field(default_factory=dict)  # 单文档字段为 dict；多行为 list[dict]
    errors: list[FieldIssue] = field(default_factory=list)
    warnings: list[FieldIssue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def error_dicts(self) -> list[dict[str, str]]:
        return [e.as_dict() for e in self.errors]


def _clean_value(def_: dict[str, Any], raw: Any) -> tuple[Any, list[FieldIssue], list[FieldIssue]]:
    """返回 (clean_value, errors, warnings)。None/空串视为未提供。"""
    key: str = def_["key"]
    label: str = def_.get("label", key)
    ftype: str = def_.get("type", "string")
    errors: list[FieldIssue] = []
    warnings: list[FieldIssue] = []
    if raw is None:
        return None, errors, warnings
    if isinstance(raw, str) and raw.strip() == "":
        return None, errors, warnings

    try:
        if ftype in ("number", "amount"):
            text = str(raw)
            if isinstance(raw, str):
                text = _NUMBER_RE.sub("", text.replace(",", "").replace("，", ""))
                text = text.strip()
            value: Any = Decimal(text)
            if ftype == "amount":
                value = value.quantize(Decimal("0.01"))
            # 统一为原生 float 便于 JSON 序列化
            return float(value), errors, warnings
        if ftype == "date":
            text = str(raw).strip().replace("/", "-")
            try:
                value = datetime.strptime(text[:10], "%Y-%m-%d")
            except ValueError:
                if text.startswith(("19", "20")) and len(text) >= 8:
                    try:
                        value = datetime.strptime(text[:8], "%Y%m%d")
                    except ValueError:
                        value = None
                else:
                    value = None
            if value is None:
                errors.append(
                    FieldIssue(field=key, label=label, message=f"{label} 不是有效日期(YYYY-MM-DD): {raw}")
                )
                return None, errors, warnings
            return value.strftime("%Y-%m-%d"), errors, warnings
        if ftype == "enum":
            allowed = def_.get("enum") or []
            if allowed and str(raw) not in allowed:
                errors.append(
                    FieldIssue(field=key, label=label, message=f"{label} 取值必须在 {allowed} 内，当前: {raw}")
                )
                return str(raw), errors, warnings
            return str(raw), errors, warnings
        if ftype == "tel":
            text = str(raw).strip()
            if text and not re.fullmatch(r"[\d+\-\s()]{5,30}", text):
                errors.append(FieldIssue(field=key, label=label, message=f"{label} 不是合法电话: {raw}"))
            return text, errors, warnings
        if ftype == "id_card":
            text = str(raw).strip()
            if text and not re.fullmatch(r"[0-9Xx]{15,18}", text):
                errors.append(FieldIssue(field=key, label=label, message=f"{label} 不是合法证件号: {raw}"))
            return text, errors, warnings
        # string
        text = str(raw).strip()
        regex = def_.get("regex")
        if regex and text and not re.fullmatch(regex, text):
            errors.append(FieldIssue(field=key, label=label, message=f"{label} 不符合格式要求: {raw}"))
        return text, errors, warnings
    except (InvalidOperation, ValueError, TypeError):
        errors.append(FieldIssue(field=key, label=label, message=f"{label} 无法解析为{ftype}: {raw}"))
        return None, errors, warnings


def validate_fields(raw: dict[str, Any] | None, field_defs: list[dict[str, Any]], *, strict: bool = False) -> ValidationOutcome:
    """按模板校验字段；strict=True 时必填缺失计入 errors，否则计入 warnings。"""
    outcome = ValidationOutcome()
    raw = raw or {}
    provided = set(raw.keys())
    known = {d["key"] for d in field_defs}

    for unknown in provided - known:
        outcome.warnings.append(
            FieldIssue(field=unknown, label=unknown, message=f"未知字段，已忽略: {unknown}")
        )

    for def_ in field_defs:
        key = def_["key"]
        clean, errors, warnings = _clean_value(def_, raw.get(key))
        outcome.errors.extend(errors)
        outcome.warnings.extend(warnings)
        if clean is not None:
            outcome.cleaned[key] = clean
        elif key in provided:
            outcome.cleaned[key] = None  # 显式 null（模型说没有该字段）

    # 必填检查（strict 时失败才错误；OCR 初值只给 warning）。
    # 注意：key 缺失或值为 null 都视为未提供 → 人工必须补全才能 approve。
    for def_ in field_defs:
        if def_.get("required"):
            val = outcome.cleaned.get(def_["key"])
            if def_["key"] not in outcome.cleaned or val is None:
                issue = FieldIssue(
                    field=def_["key"],
                    label=def_.get("label", def_["key"]),
                    message=f"必填字段缺失: {def_.get('label', def_['key'])}",
                )
                if strict:
                    outcome.errors.append(issue)
                else:
                    outcome.warnings.append(issue)
    return outcome


def validate_rows(
    rows: list[dict[str, Any]] | None, field_defs: list[dict[str, Any]], *, strict: bool = False
) -> ValidationOutcome:
    """多行记录校验：逐行清洗，聚合 errors/warnings；返回 cleaned 为行列表。"""
    outcome = ValidationOutcome(cleaned=[])
    rows = rows or []
    if not field_defs:
        return outcome
    for idx, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            continue
        r = validate_fields(row, field_defs, strict=strict)
        outcome.errors.extend(
            FieldIssue(field=f"rows[{idx}].{e.field}", label=f"第{idx}行·{e.label}", message=e.message)
            for e in r.errors
        )
        outcome.warnings.extend(
            FieldIssue(field=f"rows[{idx}].{e.field}", label=f"第{idx}行·{e.label}", message=e.message)
            for e in r.warnings
        )
        outcome.cleaned.append(r.cleaned)
    return outcome
