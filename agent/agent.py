"""智能体中控: LangChain Agent(DeepSeek LLM + 4 个 Tool)。

Tool 装配(可插拔):
  ocr_recognize   识别支票/销售图 → 入库待确认
  correct_*       人工核对/修改识别结果(4 个子工具)
  rag_*           检索/统计已确认数据
  plot_chart      画统计图
用户提问 → Agent 自动决策调哪个 Tool → 汇总回答。
"""

from __future__ import annotations

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
