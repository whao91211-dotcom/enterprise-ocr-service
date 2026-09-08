"""单元测试：模型返回内容解析（容错 JSON 提取）。"""

import json

import pytest

from app.services.result_parser import ContentParseError, parse_content


def test_plain_json():
    content = json.dumps(
        {"text": "hello world", "fields": {"no": "123"}, "extra": 1}, ensure_ascii=False
    )
    r = parse_content(content)
    assert r.text == "hello world"
    assert r.fields == {"no": "123"}
    assert r.warnings == []
    assert r.raw_parsed["extra"] == 1


def test_markdown_fenced_json():
    content = '```json\n{"text": "t", "fields": {"a": 1}}\n```'
    r = parse_content(content)
    assert r.text == "t"
    assert r.fields == {"a": 1}


def test_json_with_surrounding_noise():
    content = '好的，识别结果如下：{"text": "t2", "fields": {}}\n请查收。'
    r = parse_content(content)
    assert r.text == "t2"
    assert r.fields == {}


def test_plain_text_fallback():
    r = parse_content("整篇就是普通文本，模型没有按 JSON 输出")
    assert r.text == "整篇就是普通文本，模型没有按 JSON 输出"
    assert r.fields == {}
    assert any("非 JSON" in w for w in r.warnings)


def test_empty_content_raises():
    with pytest.raises(ContentParseError):
        parse_content("   ")


def test_missing_text_warning():
    r = parse_content('{"fields": {"a": 1}}')
    assert r.text == ""
    assert any("text" in w for w in r.warnings)


def test_missing_fields_warning():
    r = parse_content('{"text": "only text"}')
    assert r.fields == {}
    assert any("fields" in w for w in r.warnings)


def test_non_object_json_warning_path():
    # 数组或标量 JSON：无法按对象使用 → 走非 JSON 降级
    r = parse_content("[1,2,3]")
    assert r.text == "[1,2,3]"
