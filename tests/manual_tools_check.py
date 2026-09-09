"""Correct/RAG/Plot 三插件离线测试(不经真实模型/网络)。"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.correct_tool import (  # noqa: E402
    correct_confirm,
    correct_list_docs,
    correct_show_rows,
    correct_update_row,
)
from tools.plot_tool import plot_chart  # noqa: E402
from tools.rag_tool import rag_query, rag_summarize  # noqa: E402

if __name__ == "__main__":
    print("========== Tool2 Correct 测试 ==========")
    print("--- 1. correct_list_docs ---")
    print(correct_list_docs.invoke({}))
    print()

    print("--- 2. correct_show_rows(测试图A) ---")
    shown = correct_show_rows.invoke({"file_name": "测试图A.png"})
    print(shown)
    print()

    print("--- 3. correct_update_row(人工修正 item) ---")
    m = re.search(r"#(\d+)", shown)
    rid = int(m.group(1))
    print(correct_update_row.invoke({"row_id": rid, 'fields': '{"item": "掃除機-修正", "price": "19200.00"}'}))
    print()

    print("--- 4. correct_confirm(确认 C 图) ---")
    print(correct_confirm.invoke({"file_name": "测试图C.png", "reviewer": "小李"}))
    print()

    print("========== Tool3 RAG 测试 ==========")
    print("--- rag_query(掃除機) ---")
    print(rag_query.invoke({"query": "掃除機", "top_k": 5}))
    print()
    print("--- rag_query(不存在的商品) ---")
    print(rag_query.invoke({"query": "XYZ不存在商品", "top_k": 5}))
    print()
    print("--- rag_summarize(按 item) ---")
    print(rag_summarize.invoke({"group_by": "item", "filter_query": ""}))
    print()
    print("--- rag_summarize(按 desc, 过滤甲公司) ---")
    print(rag_summarize.invoke({"group_by": "desc", "filter_query": "甲"}))
    print()

    print("========== Tool4 Plot 测试 ==========")
    print("--- plot_chart(按item柱状图) ---")
    print(plot_chart.invoke({"group_by": "item", "chart_type": "bar", "filter_query": ""}))
    print("--- plot_chart(按desc饼图) ---")
    print(plot_chart.invoke({"group_by": "desc", "chart_type": "pie", "filter_query": ""}))
