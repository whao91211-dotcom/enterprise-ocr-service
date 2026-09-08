"""可插拔文档类目模板注册表（多类目扩展机制）。

课程拓展目标：扩展其他类目文档 + 意图识别分配任务。
当前训练数据仅含 sales 单类目，故只注册 sales；新增类目(如发票/合同)时
只需在此 REGISTRY 加一项 + 提供对应识别模板即可，意图路由/OCR Tool 自动适配。

类目条目字段:
  code        类目代码(唯一)
  name        中文名
  doc_type    传给 OCR 服务 /ocr/recognize 的 doc_type 参数
  intent_words 意图识别关键词(用于 classify 命中该类目)
"""

from __future__ import annotations

from typing import Any

TEMPLATE_REGISTRY: dict[str, dict[str, Any]] = {
    "sales": {
        "code": "sales",
        "name": "销售单据",
        "doc_type": "sales",
        "intent_words": ["销售", "销售单据", "sales", "商品", "采购", "买了"],
    },
    # 示例：扩展发票类目时取消注释并实现对应 OCR 模板
    # "invoice": {
    #     "code": "invoice",
    #     "name": "发票",
    #     "doc_type": "invoice",
    #     "intent_words": ["发票", "invoice", "开票", "税号", "价税合计"],
    # },
}


def list_templates() -> list[dict[str, Any]]:
    """列出已注册类目（供意图路由/前端展示）。"""
    return list(TEMPLATE_REGISTRY.values())


def get_template(code: str) -> dict[str, Any] | None:
    return TEMPLATE_REGISTRY.get(code)


def detect_doc_type(text: str) -> str | None:
    """按关键词猜测文本提及的文档类目 code（无命中返回 None，默认按 sales）。"""
    t = (text or "").lower()
    for code, tpl in TEMPLATE_REGISTRY.items():
        for w in tpl.get("intent_words", []):
            if w.lower() in t:
                return code
    return None
