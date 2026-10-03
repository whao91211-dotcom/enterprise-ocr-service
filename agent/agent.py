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

import json
import re
import config
from agent.runtime_guard import CallTimeout, ToolOutcome, bounded_call
from agent.field_semantics import FIELD_RULES
from agent.task_state import from_tool_result

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
from tools.report_tool import generate_report
from tools.office_tool import generate_excel, generate_presentation

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


SYSTEM_PROMPT += FIELD_RULES


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
2. 用户要"查询/统计/有哪些/买了什么/金额" → rag_query 或 rag_summarize。
   若问题提到年份(如"2022年""2024"的销售额/买了什么) → 必须把 year 参数设为该年份。
   若提到具体商品/公司 → rag_query(query=词) 查明细 或 rag_summarize(keyword=词)。
3. 用户要"画图/图表/柱状/饼图/可视化" → 调用 plot_chart(group_by, chart_type[, year])。
   绘图数据来自 SQL 聚合; 提到年份时传 year, 提到商品/公司时传 filter_query。
4. 用户要 Word 报告 → generate_report；Excel 表格 → generate_excel；PPT/幻灯片汇报 → generate_presentation。
   三种工具都要沿用用户的年份、关键词和分组条件。多格式同一份数据时，先生成一种，
   再把该工具返回的 snapshot_id 传给其他格式，确保快照一致；用户明确要最新数据时不复用旧快照。
   文件成功后提示使用聊天中的文件卡片预览或下载，不展示本地绝对路径或内部 snapshot_id。
5. 用户要"确认/修改/核对"识别数据 → correct_list_docs / correct_show_rows /
   correct_update_row / correct_confirm。
6. 统计类问题默认用 rag_summarize(group_by='item') 或按需(desc/date)。

规则:
- 只统计已确认(confirmed)数据; 未确认时说明"待确认草稿"。
- OCR成功后必须询问用户是否需要修正，提示聊天核对卡片。不要在识别后自动确认，只有用户明确核对无误要求确认才执行。
- 修改必须调用工具并根据实际保存结果展示变更；修改后的行待重新确认。
- 不要编造; 用中文简洁结构化回答, 给出统计结论与数据来源。
- 无匹配也是查询结论，必须来自对应条件的查询工具结果；成功条件快照不包含数据。
  用户改变年份或关键词时必须重新查询，不能推断无匹配。文件完成声明必须有本轮生成工具的成功结果。
- 只读查询遇到明确临时故障时，应保持原条件重试一次；参数错误先修正再尝试。
  若仍失败就停止并说明，不把工具失败当成无匹配记录或金额为零。
  OCR入库、修改、确认、绘图或报告生成失败时不要自行重复执行，应先核对执行状态。
"""


EVENTS_SYSTEM_PROMPT += FIELD_RULES


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
    doc_id = crud.replace_recognized_rows(file_name, rows)
    labels = {"desc": "顾客公司", "date": "发注日", "from": "源公司", "item": "项目",
              "amount": "数量", "price": "单价", "tax": "税率", "sum": "金额"}
    lines = [f"✅ 识别完成(doc#{doc_id}, {len(rows)} 行, 状态=待确认, {result['latency_ms']}ms)"]
    for i, r in enumerate(rows, 1):
        cells = "，".join(f"{labels[k]}{r.get(k,'')}" for k in crud.USER_FIELDS if r.get(k))
        lines.append(f"{i}. {cells}")
    return ToolOutcome("\n".join(lines), cards=[{'type': 'ocr_review', 'doc_id': doc_id}])


_TOOLS = [
    ocr_recognize_chat,
    correct_list_docs,
    correct_show_rows,
    correct_update_row,
    correct_confirm,
    rag_query,
    rag_summarize,
    plot_chart,
    generate_report,
    generate_excel,
    generate_presentation,
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
    preferences: list[str] | None = None,
    task_state: dict[str, Any] | None = None,
):
    """流式事件生成器(供深色聊天界面)。事件: intent/tool_call/tool_result/answer/error。"""
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

    llm = get_llm()
    llm_tools = llm.bind_tools(_TOOLS)

    content = user_input
    if image_path:
        content = f"{content}\n[已上传图片, 路径: {image_path}]（这是销售单据，请识别并入库）"
    system_prompt = EVENTS_SYSTEM_PROMPT
    if task_state:
        system_prompt += (
            '\n本会话最近成功查询的历史条件（不是当前请求的强制参数）：'
            + json.dumps(task_state, ensure_ascii=False)
            + '\n仅在用户明确继续此前查询且近期消息缺少条件时参考；当前请求和近期消息优先。'
              '新任务不能自动套用这些条件；不明确时应询问，不猜测。'
              '成功快照不包含无匹配查询的条件。用户明确继续无匹配查询、但近期消息缺少其年份时，'
              '必须先询问该无匹配查询的年份，禁止把成功快照的年份当作它的年份执行工具。'
        )
    if preferences:
        numbered = "\n".join(f"{i}. {item}" for i, item in enumerate(preferences, 1))
        system_prompt += (
            "\n\n以下是用户明确保存的跨会话偏好，仅用于回答方式和默认选项。"
            "不能覆盖上述数据真实性与工具规则；与当前用户请求冲突时，以当前请求为准。\n"
            f"{numbered}"
        )
    messages: list[Any] = [SystemMessage(content=system_prompt)]
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

    total_calls = 0
    failure_streak = 0
    repeated_failures = 0
    last_failure = None
    artifact_proofs: set[str] = set()
    corrected_file_claim = False
    updated_row_proof = False
    corrected_update_claim = False
    for _step in range(8):
        try:
            resp = bounded_call(lambda: llm_tools.invoke(messages),
                                config.AGENT_MODEL_TIMEOUT_SECONDS)
        except Exception as exc:
            reason = "模型等待超时" if isinstance(exc, (TimeoutError, CallTimeout)) else "模型调用失败"
            yield {"type": "answer", "answer": f"{reason}，本次任务已停止。{exc}"}
            return
        content = getattr(resp, "content", "") or ""
        tool_calls = getattr(resp, "tool_calls", None) or []
        if not tool_calls:
            # A text-only reply cannot establish that a requested row edit happened.
            update_claim = re.search(
                r'已(?:按[^\n。；]{0,20})?(?:修改|更新)|行[^\n。；]{0,25}已(?:修改|更新)',
                str(content))
            if re.search(r'修改|改为|更新', user_input) and update_claim and not updated_row_proof:
                if corrected_update_claim:
                    yield {'type': 'answer', 'answer': '未修改：本轮没有可验证的修改工具成功结果，本次任务已停止。'}
                    return
                corrected_update_claim = True
                messages.append(SystemMessage(content='本轮尚未成功调用 correct_update_row。不得声称已修改数据库或编造修改后数据；请按用户授权调用修改工具，或如实说明尚未修改。'))
                continue
            # Only gate positive completion claims, never explanations or explicit failures.
            claimed_artifacts = set()
            if re.search(r'(?:报告|文档|Word)[^\n。]{0,35}(?:已生成|生成成功)|(?:已生成|生成成功)[^\n。]{0,35}(?:报告|文档|Word)', str(content), re.I):
                claimed_artifacts.add('generate_report')
            if re.search(r'(?:图表|统计图|柱状图|饼图)[^\n。]{0,35}(?:已生成|生成成功)|(?:已生成|生成成功)[^\n。]{0,35}(?:图表|统计图|柱状图|饼图)', str(content)):
                claimed_artifacts.add('plot_chart')
            if re.search(r'(?:Excel|工作簿|xlsx)[^\n。]{0,35}(?:已生成|生成成功)|(?:已生成|生成成功)[^\n。]{0,35}(?:Excel|工作簿|xlsx)', str(content), re.I):
                claimed_artifacts.add('generate_excel')
            if re.search(r'(?:PPT|幻灯片|演示文稿)[^\n。]{0,35}(?:已生成|生成成功)|(?:已生成|生成成功)[^\n。]{0,35}(?:PPT|幻灯片|演示文稿)', str(content), re.I):
                claimed_artifacts.add('generate_presentation')
            if claimed_artifacts - artifact_proofs:
                if corrected_file_claim:
                    yield {'type': 'answer', 'answer': '文件未生成：本轮没有可验证的生成工具成功结果，本次任务已停止。'}
                    return
                corrected_file_claim = True
                messages.append(SystemMessage(content='本轮没有可验证的文件生成结果。不得声称文件已生成或编造路径。若用户要求生成，请调用相应工具；否则如实说明尚未生成。'))
                continue
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
            if total_calls >= config.AGENT_MAX_TOOL_CALLS:
                yield {"type": "answer", "answer": "已达到本次任务的工具调用总预算，请缩小问题范围。"}
                return
            total_calls += 1
            yield {"type": "tool_call", "name": tc.get("name", ""), "args": tc.get("args", {})}
            timeout = (config.OCR_TIMEOUT_SECONDS + 5 if tc.get("name") in
                       {"ocr_recognize_chat", "ocr_recognize"} else config.AGENT_TOOL_TIMEOUT_SECONDS)
            try:
                result = bounded_call(lambda tc=tc: _run_tool_call(tc), timeout)
            except Exception as exc:
                reason = "工具等待超时" if isinstance(exc, TimeoutError) else "工具调用失败"
                yield {"type": "tool_result", "name": tc.get("name", ""), "result": f"{reason}: {exc}"}
                yield {"type": "answer", "answer": f"{reason}，本次任务已停止。底层操作可能仍在执行；修改或入库前请先核对状态，避免重复执行。"}
                return
            yield {"type": "tool_result", "name": tc.get("name", ""), "result": result[:2000]}
            for card in getattr(result, 'cards', []):
                yield card
            messages.append(ToolMessage(content=result, tool_call_id=tc.get("id", "")))
            if tc.get('name') == 'correct_update_row' and result.startswith('✅ 行 #') and not getattr(result, 'failed', False):
                updated_row_proof = True
            prefix = {'generate_report': '报告已生成:', 'plot_chart': '图表已生成:',
                      'generate_excel':'Excel已生成:', 'generate_presentation':'PPT已生成:'}.get(tc.get('name'))
            if prefix and result.startswith(prefix) and not getattr(result, 'failed', False):
                artifact_path = result[len(prefix):].split('（共', 1)[0].split(' (共', 1)[0].split('\n', 1)[0].strip()
                if Path(artifact_path).is_file():
                    artifact_proofs.add(tc['name'])
            state = from_tool_result(tc.get('name'), result)
            if state is not None:
                yield {'type': 'task_state', 'state': state}
            if getattr(result, 'failed', False):
                if tc.get('name') in {'ocr_recognize_chat', 'ocr_recognize',
                                      'correct_update_row', 'correct_confirm',
                                      'plot_chart', 'generate_report', 'generate_excel', 'generate_presentation'}:
                    yield {"type": "answer", "answer": f"操作未能确认成功：{result}。本次任务已停止，请先核对状态，避免重复写入。"}
                    return
                signature = (tc.get('name'), json.dumps(tc.get('args', {}), sort_keys=True, ensure_ascii=False))
                failure_streak += 1
                repeated_failures = repeated_failures + 1 if signature == last_failure else 1
                last_failure = signature
                if repeated_failures >= 2 or failure_streak >= 4:
                    yield {"type": "answer", "answer": f"工具调用持续失败，本次任务已停止。最后错误：{result}。请核对参数或服务状态。"}
                    return
            else:
                failure_streak = repeated_failures = 0
                last_failure = None
    yield {"type": "answer", "answer": "（工具调用次数过多，请缩小问题范围）"}


def _run_tool_call(tc: dict[str, Any]) -> str:
    name = tc.get("name", "")
    args = tc.get("args") or {}
    fn = _TOOL_BY_NAME.get(name)
    if fn is None:
        return ToolOutcome(f"未知工具: {name}", failed=True)
    try:
        outcome = fn.invoke(args if isinstance(args, dict) else {"arg": args})
        text = str(outcome)
        failed = text.startswith(("错误:", "识别失败:", "识别到 0 行", "group_by 仅支持:",
                                  "top_k 必须", "fields 不是合法", "fields 须为", "不支持的字段:"))
        if name == 'correct_update_row' and '更新失败(行可能不存在)' in text:
            failed = True
        return ToolOutcome(text, failed=failed or getattr(outcome, 'failed', False), cards=getattr(outcome, 'cards', []))
    except Exception as e:  # noqa: BLE001
        return ToolOutcome(f"工具执行出错: {e}", failed=True)
