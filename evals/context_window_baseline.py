"""Capture the current Agent context without calling DeepSeek or OCR.

The fake model records the actual LangChain messages assembled by
run_agent_events. Token counts use cl100k_base as a proxy, not DeepSeek usage.
Run: python evals/context_window_baseline.py
"""

from __future__ import annotations

import argparse
import json
import sys
from importlib.metadata import version
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import tiktoken  # noqa: E402

ENCODING = tiktoken.get_encoding("cl100k_base")


class FakeResponse:
    def __init__(self, content: str = "离线固定回答", tool_calls: list | None = None):
        self.content = content
        self.tool_calls = tool_calls or []


class CaptureModel:
    def __init__(self, call_tool: bool = False):
        self.call_tool = call_tool
        self.calls: list[list] = []
        self.tools: list = []

    def bind_tools(self, tools):
        self.tools = list(tools)
        return self

    def invoke(self, messages):
        self.calls.append(list(messages))
        if self.call_tool and len(self.calls) == 1:
            return FakeResponse("", [{
                "name": "rag_query", "args": {"query": "合成数据"}, "id": "offline-call-1",
            }])
        return FakeResponse()


def _message_text(message) -> str:
    content = str(message.content)
    tool_calls = getattr(message, "tool_calls", None)
    if tool_calls:
        content += json.dumps(tool_calls, ensure_ascii=False, sort_keys=True)
    return content


def _tokens(text: str) -> int:
    return len(ENCODING.encode(text))


def _components(messages: list) -> dict:
    result: dict[str, dict[str, int]] = {}
    for message in messages:
        role = getattr(message, "type", "unknown")
        category = "tool_result" if role == "tool" else role
        content = _message_text(message)
        entry = result.setdefault(category, {"messages": 0, "characters": 0,
                                             "proxy_tokens": 0})
        entry["messages"] += 1
        entry["characters"] += len(content)
        entry["proxy_tokens"] += _tokens(content)
    return result


def _case(agent_module, name: str, question: str, history: list[dict],
          preferences: list[str] | None = None, tool_result: str | None = None,
          required_history_fact: str | None = None) -> dict:
    model = CaptureModel(call_tool=tool_result is not None)
    with patch.object(agent_module, "get_llm", return_value=model), \
         patch.object(agent_module, "_run_tool_call", return_value=tool_result or ""):
        events = list(agent_module.run_agent_events(
            question, history, preferences=preferences or [],
        ))
    assert model.calls and any(event["type"] == "answer" for event in events)
    first, last = model.calls[0], model.calls[-1]
    selected_history = first[1:-1]
    tool_spec = json.dumps([
        {"name": tool.name, "description": tool.description, "args": tool.args}
        for tool in model.tools
    ], ensure_ascii=False, sort_keys=True, default=str)
    result = {
        "case": name,
        "input_history_messages": len(history),
        "retained_history_messages": len(selected_history),
        "dropped_history_messages": len(history) - len(selected_history),
        "preference_count": len(preferences or []),
        "model_invocations": len(model.calls),
        "initial_components": _components(first),
        "initial_message_proxy_tokens": sum(_tokens(_message_text(m)) for m in first),
        "final_message_proxy_tokens": sum(_tokens(_message_text(m)) for m in last),
        "tool_schema_proxy_tokens": _tokens(tool_spec),
    }
    if required_history_fact:
        result["required_history_fact_retained"] = any(
            required_history_fact in str(message.content) for message in selected_history
        )
    if tool_result is not None:
        emitted = next(event["result"] for event in events
                       if event["type"] == "tool_result")
        tool_messages = [message for message in last if message.type == "tool"]
        result["tool_result_characters_to_model"] = len(str(tool_messages[0].content))
        result["tool_result_characters_to_ui"] = len(emitted)
        assert result["tool_result_characters_to_model"] == len(tool_result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path,
                        help="Write the reproducible result as UTF-8 JSON")
    args = parser.parse_args()
    from agent import agent as agent_module

    short_history = [
        {"role": "user", "content": "统计 2024 年甲公司销售额"},
        {"role": "assistant", "content": "甲公司 2024 年销售额为 100。"},
    ]
    long_history = short_history + [
        {"role": "user" if i % 2 == 0 else "assistant",
         "content": f"第 {i + 1} 条无关对话"}
        for i in range(12)
    ]
    maximum_preferences = [f"偏好 {i + 1}：" + "请使用简洁的中文回答。" * 15
                           for i in range(20)]
    large_tool_result = "合成销售明细：甲公司，2024 年，金额 100。\n" * 400
    cases = [
        _case(agent_module, "short_followup", "那乙公司呢？", short_history,
              required_history_fact="2024"),
        _case(agent_module, "long_followup", "那乙公司呢？", long_history,
              required_history_fact="2024"),
        _case(agent_module, "long_single_reply", "继续", [
            {"role": "user", "content": "请分析数据"},
            {"role": "assistant", "content": "合成分析结果。" * 1200},
        ]),
        _case(agent_module, "maximum_preferences", "请概述销售情况", [],
              preferences=maximum_preferences),
        _case(agent_module, "large_tool_result", "查询合成数据", [],
              tool_result=large_tool_result),
    ]
    assert cases[0]["required_history_fact_retained"] is True
    assert cases[1]["required_history_fact_retained"] is False
    assert cases[1]["retained_history_messages"] == 10
    assert cases[-1]["tool_result_characters_to_ui"] == 2000
    report = {
        "measurement": "offline context text at actual Agent model boundary",
        "tokenizer_proxy": f"tiktoken cl100k_base {version('tiktoken')}",
        "bound_tool_count": len(agent_module._TOOLS),
        "cases": cases,
        "limitations": [
            "Proxy tokens are not DeepSeek billing/input tokens",
            "Message role framing and provider serialization are not counted",
            "Tool schema tokens use local JSON serialization, not the provider payload",
            "Fake model and synthetic data: no answer quality, latency, cost, or OCR measured",
        ],
    }
    serialized = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(serialized, encoding="utf-8")
    else:
        print(serialized, end="")


if __name__ == "__main__":
    main()
