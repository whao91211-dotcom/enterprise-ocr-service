"""Prompt 构建：按单据模板生成 OCR 指令（JSON 字段契约 或 CSV 多行记录契约）。"""

from __future__ import annotations

import json
from typing import Any

_TYPE_HINT = {
    "string": "字符串",
    "number": "数字",
    "amount": "金额数字(不含货币符号，保留两位小数)",
    "date": "日期，格式 YYYY-MM-DD",
    "enum": "枚举(必须取给定值之一)",
    "tel": "电话/手机号字符串",
    "id_card": "证件号码字符串",
}


def build_csv_instruction(template_code: str, field_defs: list[dict[str, Any]]) -> str:
    """生成销售单据多行 CSV 识别指令（严格复刻真实 InternVL 可用 prompt）。

    实测：仅「# 任务描述 + 输出标准 CSV」短 prompt 才能在 9052 稳定返回 200；
    追加字段定义/输出说明会触发 500/超时。列序固定为 desc,item,amount,price,tax,sum，
    由 field_defs 的顺序在解析阶段映射，不写入 prompt。
    """
    return "# 任务描述\n你需要对图像中的销售消息进行结构化信息提取，输出一个标准CSV格式的数据。"


def build_instruction(template_code: str, template_name: str, field_defs: list[dict[str, Any]]) -> str:
    """生成发给视觉模型的识别指令。微调模型的输出约定见 README『模型契约』。"""
    lines: list[str] = [
        f"你是企业{template_name}(类型代码 {template_code})的 OCR 文字识别引擎。",
        "请识别图片中的全部文字与单据字段，仅输出一个 JSON 对象（不要输出任何其他文字或 markdown），结构如下：",
        '{"text": "图片中全部文字内容，按阅读顺序排列", "fields": { <字段key>: <字段值> }}',
        "其中 text 必须为完整原文；fields 中仅输出以下单据字段（key 严格使用给定 key 命名）：",
    ]
    for f in field_defs:
        req = "必填" if f.get("required") else "可选"
        hint = _TYPE_HINT.get(f.get("type", "string"), "字符串")
        extra = ""
        if f.get("enum"):
            extra = f"，取值只能为 {f['enum']}"
        lines.append(f"- {f['key']}（{f.get('label', f['key'])}）：{hint}，{req}{extra}；识别不到时该字段值为 null")
    lines.append("若某字段在图片中确实不存在，fields 中对应值为 null，text 仍输出全部原文。")
    return "\n".join(lines)


def build_instruction_json(template_code: str, template_name: str, field_defs: list[dict[str, Any]]) -> str:
    """同上，但以 JSON Schema 形态描述（供部分模型更稳定输出）。"""
    props = {f["key"]: _prop_desc(f) for f in field_defs}
    schema = {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "图片中全部文字内容，按阅读顺序排列"},
            "fields": {"type": "object", "properties": props, "required": [f["key"] for f in field_defs if f.get("required")]},
        },
        "required": ["text", "fields"],
    }
    return (
        f"你是企业{template_name}(类型代码 {template_code})的 OCR 文字识别引擎。\n"
        "识别图片中的文字与单据字段，严格按以下 JSON Schema 输出一个 JSON 对象，"
        "不得输出任何额外文字或 markdown：\n"
        f"{json.dumps(schema, ensure_ascii=False, indent=2)}"
    )


def _prop_desc(f: dict[str, Any]) -> dict[str, Any]:
    t = f.get("type", "string")
    desc = f.get("label", f["key"])
    hint = _TYPE_HINT.get(t, "字符串")
    desc = f"{desc}；{hint}；识别不到时为 null"
    if f.get("enum"):
        desc += f"；只能取值 {f['enum']}"
    prop: dict[str, Any] = {"type": "string", "description": desc}
    if t in ("number", "amount"):
        prop = {"type": ["number", "null"], "description": desc}
    return prop
