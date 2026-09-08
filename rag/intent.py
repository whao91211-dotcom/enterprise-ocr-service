"""意图识别模块：显式规则分类器，识别用户意图并映射到对应 Tool。

作用：
  1. 作为 function-calling 的【兜底/预处理】：无 Key 或模型未触发工具时，
     也能演示"意图识别 → 任务分配 → 多Tool"的框架。
  2. 可解释性强（课程汇报可展示规则与判定逻辑），并预留 LLM 意图识别接口。

意图类型（当前文档类目）:
  - ocr_recognize   用户要求识别图片/提取单据信息
  - rag_query       查询已入库销售数据（统计/明细/某公司买了什么）
  - help            打招呼/问能力
  - unknown         其它
"""

from __future__ import annotations

import re
from typing import Literal

Intent = Literal["ocr_recognize", "rag_query", "help", "unknown"]

# ---- 关键词特征 ----
_OCR_MARKERS = [
    "识别",
    "提取",
    "读一下",
    "认一下",
    "单据信息",
    "图片",
    "照片",
    "图里",
    "ocr",
    "upload",
    "这张图",
    "sample",
    ".png",
    ".jpg",
    ".jpeg",
]
_RAG_MARKERS = [
    "查",
    "查询",
    "搜索",
    "找一下",
    "统计",
    "买了",
    "采购",
    "销售",
    "有多少",
    "金额",
    "单价",
    "数量",
    "哪张图",
    "数据",
    "记录",
    "明细",
    "几台",
    "公司",
    "2024",
    "2025",
    "年度",
]
_HELP_MARKERS = ["你好", "您好", "hi", "hello", "help", "你能做什么", "帮助", "介绍"]


def classify(text: str) -> Intent:
    """规则意图分类：RAG 优先(查询意图词多)，其次 OCR，其次 help。"""
    t = (text or "").strip().lower()

    # 含图片路径/明确识别动词 → OCR
    ocr_hit = sum(1 for m in _OCR_MARKERS if m.lower() in t)
    rag_hit = sum(1 for m in _RAG_MARKERS if m.lower() in t)

    # 有 .png/.jpg 或"识别/提取/图片"开头强信号
    has_file = bool(re.search(r"[\\/][\w\-. ]+\.(png|jpe?g|webp|bmp)", t))
    if has_file and ocr_hit:
        return "ocr_recognize"

    if rag_hit >= 2 and rag_hit > ocr_hit:
        return "rag_query"
    if ocr_hit >= 1 and ocr_hit >= rag_hit:
        return "ocr_recognize"
    if rag_hit >= 1:
        return "rag_query"
    if any(m in t for m in _HELP_MARKERS):
        return "help"
    return "unknown"


def suggest_route(intent: Intent) -> str | None:
    """意图 → 建议调用的 Tool 名（None=直接回答）。"""
    return {
        "ocr_recognize": "ocr_recognize",
        "rag_query": "rag_query",
    }.get(intent)
