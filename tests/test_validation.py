"""单元测试：字段校验（类型清洗/必填/enum/日期/金额）。"""

from app.services.validation import validate_fields

INVOICE_DEFS = [
    {"key": "invoice_no", "label": "发票号码", "type": "string", "required": True},
    {"key": "invoice_date", "label": "开票日期", "type": "date", "required": True},
    {"key": "seller", "label": "销售方", "type": "string", "required": True},
    {"key": "amount", "label": "金额", "type": "amount", "required": True},
    {"key": "total", "label": "价税合计", "type": "amount", "required": True},
    {"key": "pay_type", "label": "支付方式", "type": "enum", "enum": ["现金", "转账", "支票"], "required": False},
    {"key": "contact", "label": "联系电话", "type": "tel", "required": False},
]

VALID = {
    "invoice_no": "1100234567",
    "invoice_date": "2025/01/08",
    "seller": "示例公司",
    "amount": "1,234.56元",
    "total": 1130.0,
    "pay_type": "转账",
    "contact": "010-88886666",
}


def test_valid_clean_types():
    out = validate_fields(VALID, INVOICE_DEFS, strict=True)
    assert out.ok, out.error_dicts()
    assert out.cleaned["amount"] == 1234.56
    assert out.cleaned["total"] == 1130.0
    assert out.cleaned["invoice_date"] == "2025-01-08"
    assert out.cleaned["pay_type"] == "转账"


def test_missing_required_strict_error_but_non_strict_warning():
    bad = {k: v for k, v in VALID.items() if k != "invoice_no"}
    strict = validate_fields(bad, INVOICE_DEFS, strict=True)
    assert not strict.ok
    assert any(e.field == "invoice_no" for e in strict.errors)
    assert strict.error_dicts()[0]["label"] == "发票号码"

    loose = validate_fields(bad, INVOICE_DEFS, strict=False)
    assert loose.ok
    assert any(w.field == "invoice_no" for w in loose.warnings)


def test_required_null_means_missing():
    bad = dict(VALID)
    bad["invoice_no"] = None
    out = validate_fields(bad, INVOICE_DEFS, strict=True)
    assert not out.ok
    assert any(e.field == "invoice_no" for e in out.errors)


def test_bad_enum_and_tel():
    bad = dict(VALID)
    bad["pay_type"] = "扫码"
    bad["contact"] = "不是电话"
    out = validate_fields(bad, INVOICE_DEFS, strict=True)
    assert not out.ok
    fields = {e.field for e in out.errors}
    assert {"pay_type", "contact"} <= fields


def test_bad_amount_and_date():
    bad = dict(VALID)
    bad["amount"] = "abc"
    bad["invoice_date"] = "2025-13-40"
    out = validate_fields(bad, INVOICE_DEFS, strict=True)
    assert not out.ok
    fields = {e.field for e in out.errors}
    assert {"amount", "invoice_date"} <= fields


def test_unknown_keys_warned_and_dropped():
    raw = dict(VALID)
    raw["hacker_field"] = "x"
    out = validate_fields(raw, INVOICE_DEFS, strict=True)
    assert out.ok
    assert "hacker_field" not in out.cleaned
    assert any("未知字段" in w.message for w in out.warnings)


def test_null_value_cleaned_as_null():
    raw = dict(VALID)
    raw["pay_type"] = None  # 可选字段显式 null
    out = validate_fields(raw, INVOICE_DEFS, strict=True)
    assert out.ok
    assert out.cleaned["pay_type"] is None
