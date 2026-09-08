"""result_parser 单元测试：CSV 多行容错解析。"""

import pytest

from app.services.result_parser import ContentParseError, parse_csv_rows


def test_parse_basic_rows():
    content = "a,1,2\nb,3,4\n"
    r = parse_csv_rows(content)
    assert len(r.rows) == 2
    assert r.rows[0] == ["a", "1", "2"]


def test_skip_blank_and_comment():
    content = "# 说明行\n\na,1,2\n\nb,3,4\n"
    r = parse_csv_rows(content)
    assert len(r.rows) == 2


def test_skip_header_row():
    content = "desc,date,item\na,1,2\n"
    r = parse_csv_rows(content)
    assert len(r.rows) == 1
    assert r.rows[0] == ["a", "1", "2"]
    assert r.warnings  # 提示跳过了表头


def test_quoted_comma():
    content = '"含,逗号公司",1,2\n'
    r = parse_csv_rows(content)
    assert r.rows[0][0] == "含,逗号公司"


def test_empty_raises():
    with pytest.raises(ContentParseError):
        parse_csv_rows("   ")


def test_no_data_rows_raises():
    with pytest.raises(ContentParseError):
        parse_csv_rows("# 只有注释\n\n")
