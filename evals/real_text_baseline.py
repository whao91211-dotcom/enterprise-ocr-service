"""Small live-LLM baseline using synthetic, confirmed sales data only.

Requires a configured DEEPSEEK_API_KEY. Does not call the OCR service.
Run from the repository root: python evals/real_text_baseline.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _seed_sales() -> None:
    from db import crud
    from db.database import init_db

    init_db()
    cases = [
        ("synthetic-2024-a.png", "甲公司", "2024-01-10", "商品A", "100.00"),
        ("synthetic-2024-b.png", "乙公司", "2024-05-20", "商品B", "200.00"),
        ("synthetic-2025-b.png", "乙公司", "2025-05-20", "商品C", "900.00"),
    ]
    for file_name, customer, date, item, total in cases:
        doc_id = crud.upsert_document(file_name, None)
        crud.insert_rows(doc_id, [{
            "desc": customer,
            "date": date,
            "from": "示例供货商",
            "item": item,
            "amount": "1",
            "price": total,
            "tax": "0",
            "sum": total,
        }])
        crud.confirm_doc(doc_id, "synthetic-eval")


def _run(question: str, history: list[dict]) -> dict:
    from agent.agent import run_agent_events

    start = time.perf_counter()
    events = list(run_agent_events(question, history))
    elapsed_ms = round((time.perf_counter() - start) * 1000)
    return {
        "question": question,
        "history_messages": len(history),
        "elapsed_ms": elapsed_ms,
        "tool_calls": [
            {"name": event["name"], "args": event["args"]}
            for event in events if event["type"] == "tool_call"
        ],
        "tool_results": [
            event["result"] for event in events if event["type"] == "tool_result"
        ],
        "answer": next(
            (event["answer"] for event in events if event["type"] == "answer"), None
        ),
        "errors": [event for event in events if event["type"] == "error"],
    }


def main() -> None:
    import config

    if not config.DEEPSEEK_API_KEY:
        raise SystemExit("DEEPSEEK_API_KEY is not configured")

    with tempfile.TemporaryDirectory(prefix="agent_live_baseline_") as tmp:
        os.environ["AGENT_DATA_DIR"] = tmp
        _seed_sales()
        first = _run("统计2024年甲公司的销售额", [])
        second = _run(
            "那乙公司呢？",
            [
                {"role": "user", "content": first["question"]},
                {"role": "assistant", "content": first["answer"] or ""},
            ],
        )
        third = _run("2024年总销售额是多少？", [])

    print(json.dumps({
        "dataset": "3 synthetic confirmed rows: 2024 甲=100, 2024 乙=200, 2025 乙=900",
        "model": config.DEEPSEEK_MODEL,
        "scenarios": [first, second, third],
        "limitations": [
            "Three cases are a diagnostic sample, not a representative accuracy estimate",
            "OCR is unavailable and was not called",
            "Token usage and monetary cost are not exposed by the current event stream",
        ],
    }, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
