"""cleaner 单元测试：8 列清洗规则（date/amount/price/sum 格式化）。"""

from app.services.cleaner import clean_sales_row, clean_sales_rows


def test_row_list_full_8cols():
    row = ["顾客A", "2025年8月7日", "源社", "商品X", "4", "¥14,781.00", "0.10", "59,124.00"]
    cleaned, warnings = clean_sales_row(row)
    assert warnings == []
    assert cleaned["desc"] == "顾客A"
    assert cleaned["date"] == "2025年08月07日"  # 归一补零
    assert cleaned["from"] == "源社"
    assert cleaned["amount"] == "4"  # 取整
    assert cleaned["price"] == "14781.00"  # 去¥与千分位、两位小数
    assert cleaned["sum"] == "59124.00"


def test_row_list_short_padding():
    # 模型可能漏列：缺失部分补 ""
    row = ["顾客A", "2024-05-22", "源社", "品名"]  # 只 4 列
    cleaned, warnings = clean_sales_row(row)
    assert cleaned["amount"] == ""
    assert cleaned["price"] == ""


def test_amount_rounding():
    cleaned, _ = clean_sales_row(["c", "2024-01-01", "f", "i", "4.5", "1.00", "0.1", "4.50"])
    assert cleaned["amount"] == "5"  # 4.5 四舍五入取整


def test_date_variants():
    for raw, expect in [
        ("2025-08-07", "2025年08月07日"),
        ("2025/8/7", "2025年08月07日"),
        ("20250807", "2025年08月07日"),
        ("2024年05月22日", "2024年05月22日"),
    ]:
        cleaned, _ = clean_sales_row(["c", raw, "f", "i", "1", "1.00", "0", "1.00"])
        assert cleaned["date"] == expect, raw


def test_money_junk_removed():
    cleaned, _ = clean_sales_row(["c", "2024-01-01", "f", "i", "2", "￥1,234.50", "0.10", "2,469.00"])
    assert cleaned["price"] == "1234.50"
    assert cleaned["sum"] == "2469.00"


def test_empty_row_dropped():
    rows, warnings = clean_sales_rows([["", "", "", "", "", "", "", ""], ["a", "2024-01-01", "b", "c", "1", "1.00", "0", "1.00"]])
    assert len(rows) == 1
    assert warnings == []


def test_unparseable_money_warns_keeps_blank():
    cleaned, warnings = clean_sales_row(["c", "2024-01-01", "f", "i", "1", "abc", "0.10", "1.00"])
    assert cleaned["price"] == ""
    assert any("price" in w for w in warnings)
