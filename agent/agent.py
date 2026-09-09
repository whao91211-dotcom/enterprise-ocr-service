"""智能体中控: LangChain Agent(DeepSeek LLM + 4 个 Tool)。

Tool 装配(可插拔):
  ocr_recognize   识别支票/销售图 → 入库待确认
  correct_*       人工核对/修改识别结果(4 个子工具)
  rag_*           检索/统计已确认数据
  plot_chart      画统计图
用户提问 → Agent 自动决策调哪个 Tool → 汇总回答。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from langchain_classic.agents import AgentExecutor, create_tool_calling_agent
from langchain_core.prompts import ChatPromptTemplate

from agent.llm import get_llm
from tools.correct_tool import (
    correct_confirm,
    correct_list_docs,
    correct_show_rows,
    correct_update_row,
)
from tools.ocr_tool import ocr_recognize
from tools.plot_tool import plot_chart
from tools.rag_tool import rag_query, rag_summarize

SYSTEM_PROMPT = """你是一个企业文档处理智能体。你可以:

1. 识别单据: 用户提供图片/支票 → ocr_recognize(结果入库, 状态=待确认)。
2. 核对修改: 识别准确率约80%, 用户可能要求查看/修正识别数据 →
   correct_list_docs / correct_show_rows / correct_update_row / correct_confirm。
   只有确认(confirmed)后的数据才用于统计, 重要统计前先提示用户确认。
3. 查询统计: 用户问已确认的销售/采购情况(某公司买了什么/某商品销量金额) →
   rag_query(明细) / rag_summarize(聚合小计)。
4. 画图: 用户要求"画图/图表/可视化" → plot_chart。
5. 若数据未确认, 回答时说明是"待确认草稿"; 用确认后的数据回答并给出来源。
回答用中文, 结构清晰, 不要编造数据; 无匹配时如实说没有。"""


def build_agent() -> AgentExecutor:
    tools = [
        ocr_recognize,
        correct_list_docs,
        correct_show_rows,
        correct_update_row,
        correct_confirm,
        rag_query,
        rag_summarize,
        plot_chart,
    ]
    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", SYSTEM_PROMPT),
            ("placeholder", "{chat_history}"),
            ("human", "{input}"),
            ("placeholder", "{agent_scratchpad}"),
        ]
    )
    llm = get_llm()
    agent = create_tool_calling_agent(llm, tools, prompt)
    return AgentExecutor(agent=agent, tools=tools, verbose=False, max_iterations=8)


# 流式事件版的系统提示(强化工具路由, 尤其"画图→plot_chart")
EVENTS_SYSTEM_PROMPT = """你是一个企业文档处理智能体。根据用户意图自主选择工具:

1. 用户提供图片或要"识别/提取" → ocr_recognize(image_path)。识别结果入库(待确认)。
2. 用户要"查询/统计/有哪些/买了什么/金额" → rag_query(明细) 或 rag_summarize(聚合)。
3. 用户要"画图/图表/柱状/饼图/可视化/plot" → 必须调用 plot_chart(group_by, chart_type),
   生成图表文件并告知用户。
4. 用户要"确认/修改/核对"识别数据 → correct_list_docs / correct_show_rows /
   correct_update_row / correct_confirm。
5. 若用户直接给数据询问统计且画图, 先 rag_summarize 再 plot_chart。

规则:
- 有确认(confirmed)数据才统计; 未确认时说明"待确认草稿"。
- 不要编造; 用中文回答, 简洁结构化。回答中包含统计结论与来源。
"""


def ask(question: str, history: list[dict[str, Any]] | None = None) -> str:
    """执行一轮问答, 返回最终回答文本。"""
    executor = build_agent()
    chat_history: list[Any] = []
    if history:
        # history: [{"role": "user"/"assistant", "content": ...}]
        from langchain_core.messages import AIMessage, HumanMessage

        for m in history[-10:]:
            role = m.get("role")
            content = m.get("content", "")
            if role == "user":
                chat_history.append(HumanMessage(content=content))
            elif role == "assistant":
                chat_history.append(AIMessage(content=content))
    result = executor.invoke({"input": question, "chat_history": chat_history})
    return str(result.get("output", ""))


# ---------- 流式事件版(深色聊天界面用) ----------

from langchain_core.tools import tool  # noqa: E402


@tool
def ocr_recognize_chat(image_path: str) -> str:
    """识别一张销售单据/支票图片, 返回结构化 8 列并写入数据库(待确认)。

    Args:
        image_path: 图片文件路径(png/jpg/jpeg/webp/bmp)。
    """

    from db import crud
    from tools.ocr_client import recognize

    p = Path(image_path)
    if not p.is_file():
        return f"错误: 图片不存在: {image_path}"
    ext = p.suffix.lstrip(".").lower() or "png"
    data = p.read_bytes()
    try:
        result = recognize(data, ext)
    except Exception as e:  # noqa: BLE001
        return f"识别失败: {e}"
    rows = result["rows"]
    if not rows:
        return f"识别到 0 行。原始返回: {result['raw'][:200]}"
    file_name = p.name
    doc_id = crud.upsert_document(file_name, None)
    crud.delete_rows_by_doc(doc_id)
    crud.insert_rows(doc_id, rows)
    labels = {"desc": "顾客公司", "date": "发注日", "from": "源公司", "item": "项目",
              "amount": "数量", "price": "单价", "tax": "税率", "sum": "金额"}
    lines = [f"✅ 识别完成(doc#{doc_id}, {len(rows)} 行, 状态=待确认, {result['latency_ms']}ms)"]
    for i, r in enumerate(rows, 1):
        cells = "，".join(f"{labels[k]}{r.get(k,'')}" for k in crud.USER_FIELDS if r.get(k))
        lines.append(f"{i}. {cells}")
    return "\n".join(lines)


_TOOLS = [
    ocr_recognize_chat,
    correct_list_docs,
    correct_show_rows,
    correct_update_row,
    correct_confirm,
    rag_query,
    rag_summarize,
    plot_chart,
]
_TOOL_BY_NAME = {t.name: t for t in _TOOLS}


def _guess_intent(text: str) -> str:
    import re

    t = (text or "").lower()
    if re.search(r"\.(png|jpe?g|webp|bmp)", t) or re.search(r"识别|提取|图里|图片|读一下", t):
        return "ocr_recognize"
    if re.search(r"画图|图表|柱状|饼图|可视化", t):
        return "plot_chart"
    if re.search(r"查询|统计|多少|金额|数量|有哪些|买了|汇总|明细", t):
        return "rag_query"
    if re.search(r"确认|修改|修正|核对", t):
        return "correct"
    return "chat"


def run_agent_events(
    user_input: str,
    history: list[dict[str, Any]] | None = None,
    image_path: str | None = None,
):
    """流式事件生成器(供深色聊天界面)。事件: intent/tool_call/tool_result/answer/error。"""
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

    llm = get_llm()
    llm_tools = llm.bind_tools(_TOOLS)

    content = user_input
    if image_path:
        content = f"{content}\n[已上传图片, 路径: {image_path}]（这是销售单据，请识别并入库）"
    messages: list[Any] = [SystemMessage(content=EVENTS_SYSTEM_PROMPT)]
    if history:
        for m in history[-10:]:
            role = m.get("role")
            c = m.get("content", "")
            if role == "user":
                messages.append(HumanMessage(content=c))
            elif role == "assistant":
                messages.append(AIMessage(content=c))
    messages.append(HumanMessage(content=content))

    yield {"type": "intent", "intent": _guess_intent(user_input)}

    for _step in range(8):
        resp = llm_tools.invoke(messages)
        content = getattr(resp, "content", "") or ""
        tool_calls = getattr(resp, "tool_calls", None) or []
        if not tool_calls:
            yield {"type": "answer", "answer": content or "（模型未返回内容）"}
            return
        if content:
            yield {"type": "llm_text", "text": content}
        # assistant 消息带 tool_calls 存入, 供 tool 回填保持关联
        to_msg = getattr(resp, "to_message", None)
        assistant = to_msg() if callable(to_msg) else None
        if assistant is None:
            assistant = AIMessage(
                content=content or "",
                tool_calls=[{"name": tc.get("name", ""), "args": tc.get("args", {}),
                             "id": tc.get("id", "")} for tc in tool_calls],
            )
        messages.append(assistant)
        for tc in tool_calls:
            yield {"type": "tool_call", "name": tc.get("name", ""), "args": tc.get("args", {})}
            result = _run_tool_call(tc)
            yield {"type": "tool_result", "name": tc.get("name", ""), "result": result[:2000]}
            messages.append(ToolMessage(content=result, tool_call_id=tc.get("id", "")))
    yield {"type": "answer", "answer": "（工具调用次数过多，请缩小问题范围）"}


def _run_tool_call(tc: dict[str, Any]) -> str:
    name = tc.get("name", "")
    args = tc.get("args") or {}
    fn = _TOOL_BY_NAME.get(name)
    if fn is None:
        return f"未知工具: {name}"
    try:
        return str(fn.invoke(args if isinstance(args, dict) else {"arg": args}))
    except Exception as e:  # noqa: BLE001
        return f"工具执行出错: {e}"
