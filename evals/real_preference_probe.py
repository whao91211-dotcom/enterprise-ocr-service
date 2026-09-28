"""Two diagnostic DeepSeek calls for explicit answer-style preferences.

No OCR or business data is used. Run from the repository root.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main() -> None:
    import config
    from agent.agent import run_agent_events

    if not config.DEEPSEEK_API_KEY:
        raise SystemExit("DEEPSEEK_API_KEY is not configured")

    question = "请介绍你能提供的帮助。"
    preference = "回答开头使用【简报】作为标题"
    cases = []
    for label, preferences in (("without_preference", []),
                               ("with_preference", [preference])):
        start = time.perf_counter()
        events = list(run_agent_events(question, preferences=preferences))
        elapsed_ms = round((time.perf_counter() - start) * 1000)
        answer = next((event["answer"] for event in events
                       if event["type"] == "answer"), None)
        cases.append({
            "case": label,
            "answer": answer,
            "starts_with_marker": bool(answer and answer.lstrip().startswith("【简报】")),
            "elapsed_ms": elapsed_ms,
            "tool_calls": [event["name"] for event in events
                           if event["type"] == "tool_call"],
        })
    print(json.dumps({
        "dataset": "one synthetic question, repeated with and without an explicit style preference",
        "model": config.DEEPSEEK_MODEL,
        "preference": preference,
        "cases": cases,
        "limitations": [
            "One paired diagnostic is not a general preference-adherence rate",
            "This probes the Agent prompt, not the HTTP/SQLite path",
            "Token usage and API cost are not measured",
        ],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
