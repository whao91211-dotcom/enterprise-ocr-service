"""企业文档处理智能体（核心编排）。

用 langchain_deepseek.ChatDeepSeek（DeepSeek 官方接入）+ langchain tool calling 实现：
  用户提问 → LLM 判断调哪个 Tool(OCR 识别 / RAG 查询 / 直接回答) → 执行 Tool →
  结果回填 → LLM 给出最终回答（含引用溯源）。

Tools（可插拔扩展）:
  - ocr_recognize: 上传图片路径，识别销售单据 → 结构化 8 列 + 落盘待确认
  - rag_query:     检索已人工确认的销售数据（BM25）→ 返回命中行
扩展新文档类型 = 注册新 Tool / 新意图，见 register_tool。
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from langchain_core.tools import tool
from langchain_deepseek import ChatDeepSeek

from rag import retriever
from rag.intent import classify as classify_intent
from rag.ocr_tool import recognize_image_bytes

SYSTEM_PROMPT = (
    "你是一个企业文档处理智能体，负责销售单据的识别与查询。\n"
    "工作准则:\n"
    "1. 用户提供图片要求识别/提取单据信息时 → 调用 ocr_recognize 工具。\n"
    "2. 用户询问已入库的销售数据(某公司买过什么/某商品销量/金额统计等) → 调用 rag_query 工具。\n"
    "3. rag_query 返回的数据是人工确认过的准确记录，回答时要基于它并给出数据来源；\n"
    "   如果检索没有命中，如实说明数据库中没有相关信息，不要编造。\n"
    "4. 若用户同时给图片又提问，先识别再把结果纳入回答。\n"
    "5. 保持简洁、结构化的回答；中日文专有名词按原文输出。"
)


def build_messages(
    user_input: str, history: list[dict[str, Any]] | None = None
) -> list[dict[str, Any]]:
    """组装 system+history+user，并把显式意图识别作为引导注入 user 消息尾部。

    意图识别结果辅助 LLM 选择工具（省 token、路由更稳），但最终以 function-calling 为准。
    """
    intent = classify_intent(user_input)
    guidance = ""
    if intent == "ocr_recognize":
        guidance = "\n[系统提示] 检测到用户意图=识别图片，请调用 ocr_recognize 工具处理。"
    elif intent == "rag_query":
        guidance = "\n[系统提示] 检测到用户意图=查询已确认数据，请调用 rag_query 工具处理。"
    elif intent == "help":
        guidance = "\n[系统提示] 用户意图=询问能力，直接简要介绍你可以做什么，无需调用工具。"
    messages: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM_PROMPT}]
    if history:
        messages.extend(history[-8:])
    messages.append({"role": "user", "content": user_input + guidance})
    return messages


# ---------- Tool 定义 ----------


@tool
def ocr_recognize(image_path: str, doc_type: str = "sales") -> str:
    """识别一张单据图片并返回结构化数据。

    Args:
        image_path: 本机可访问的图片文件路径(png/jpg/jpeg/webp/bmp)。
        doc_type: 单据类目，默认 sales(销售单据)。可扩展 invoice(发票)/contract(合同)等
                  （取决于 OCR 服务已注册的模板，见 rag/templates.py）。
    """
    p = Path(image_path)
    if not p.is_file():
        return f"错误: 图片不存在: {image_path}"
    data = p.read_bytes()
    try:
        out = recognize_image_bytes(data, p.name, doc_type=doc_type)
    except Exception as e:  # noqa: BLE001
        return f"识别失败: {e}"
    if out.get("skipped"):
        return out.get("message", "该图已确认，跳过识别")
    rows = out.get("rows") or []
    if not rows:
        return f"识别到 0 行。warnings={out.get('warnings')}"
    lines = [f"识别完成: {out.get('row_count')} 行，已写入 {out.get('csv_file')}（待确认）"]
    for i, r in enumerate(rows, start=1):
        lines.append(
            f"{i}. 顾客公司:{r.get('desc', '')} 发注日:{r.get('date', '')} "
            f"源公司:{r.get('from', '')} 项目:{r.get('item', '')} "
            f"数量:{r.get('amount', '')} 单价:{r.get('price', '')} "
            f"税率:{r.get('tax', '')} 金额:{r.get('sum', '')}"
        )
    if out.get("warnings"):
        lines.append("警告: " + "; ".join(out["warnings"]))
    return "\n".join(lines)


@tool
def rag_query(question: str, top_k: int = 5) -> str:
    """在已人工确认的销售数据中检索与问题相关的记录，返回命中明细(含来源图片)。

    Args:
        question: 要检索的问题/关键词，如“某公司 2024 年买了什么空调”。
        top_k: 返回条数(默认5)。
    """
    hits = retriever.search(question, top_k=top_k)
    if not hits:
        return "（未检索到相关已确认销售数据）"
    return retriever.format_hits(hits)


@tool
def rag_rebuild() -> str:
    """识别出新图并确认后，刷新 RAG 检索索引（把新确认数据纳入检索）。"""
    n = retriever.touch()
    return f"索引已刷新，当前已确认文档 {n} 条"


# ---------- 会话编排 ----------

TOOL_REGISTRY: dict[str, Callable[..., str]] = {
    "ocr_recognize": ocr_recognize,
    "rag_query": rag_query,
    "rag_rebuild": rag_rebuild,
}


def get_llm() -> ChatDeepSeek:
    key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not key:
        env_path = Path(__file__).resolve().parents[1] / ".env"
        if env_path.exists():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                if line.startswith("DEEPSEEK_API_KEY="):
                    key = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break
    if not key:
        raise RuntimeError(
            "DEEPSEEK_API_KEY 未配置：请在项目根 .env 填入（platform.deepseek.com 申请）"
        )
    return ChatDeepSeek(model="deepseek-chat", api_key=key, temperature=0.3)


def run_turn_events(user_input: str, history: list[dict[str, Any]] | None = None):
    """执行一轮对话，产出事件流(生成器)。事件 dict:
        {"type": "intent", "intent": "..."}          意图识别结果
        {"type": "llm_content", "text": "..."}        模型的中间文本(工具调用前/后)
        {"type": "tool_start", "name": "...", "args": {...}}
        {"type": "tool_result", "name": "...", "result": "..."}
        {"type": "done", "answer": "最终回答"}
    """
    llm = get_llm()
    llm_with_tools = llm.bind_tools(list(TOOL_REGISTRY.values()))

    messages = build_messages(user_input, history)
    from rag.intent import classify as _ci

    yield {"type": "intent", "intent": _ci(user_input)}

    for _step in range(6):
        resp = llm_with_tools.invoke(messages)
        content = getattr(resp, "content", "") or ""
        tool_calls = getattr(resp, "tool_calls", None) or []
        if not tool_calls:
            yield {"type": "done", "answer": content or "（模型未返回内容）"}
            return

        if content:
            yield {"type": "llm_content", "text": content}
        # 收集本轮工具调用结果
        messages.append(
            {
                "role": "assistant",
                "content": content,
                "tool_calls": [
                    {
                        "id": tc.get("id", ""),
                        "type": "function",
                        "function": {"name": tc.get("name", ""), "arguments": tc.get("args", {})},
                    }
                    for tc in tool_calls
                ],
            }
        )
        for tc in tool_calls:
            name = tc.get("name", "")
            args = tc.get("args") or {}
            fn = TOOL_REGISTRY.get(name)
            if fn is None:
                result = f"未知工具: {name}"
            else:
                yield {"type": "tool_start", "name": name, "args": args}
                try:
                    # StructuredTool: 用 invoke(dict) 调用；也兼容裸函数
                    if hasattr(fn, "invoke"):
                        result = str(fn.invoke(args if isinstance(args, dict) else {"arg": args}))
                    else:
                        result = str(fn(**args)) if isinstance(args, dict) else str(fn(args))
                except Exception as e:  # noqa: BLE001
                    result = f"工具执行出错: {e}"
                yield {"type": "tool_result", "name": name, "result": result}
            messages.append({"role": "tool", "content": result, "tool_call_id": tc.get("id", "")})
    yield {"type": "done", "answer": "（工具调用次数过多，请缩小问题范围）"}


def run_turn(user_input: str, history: list[dict[str, Any]] | None = None) -> str:
    """执行一轮对话（便捷版）：只返回最终回答文本。"""
    for ev in run_turn_events(user_input, history):
        if ev.get("type") == "done":
            return ev["answer"]
    return "（未获得回答）"


def ask(question: str) -> str:
    """便捷入口：单轮问答。"""
    return run_turn(question)
