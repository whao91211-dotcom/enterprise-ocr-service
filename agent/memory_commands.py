"""Deterministic commands for explicit long-term memory changes."""

from __future__ import annotations


def parse_memory_command(message: str) -> tuple[str, str] | None:
    text = message.strip()
    if text == "查看记忆":
        return "list", ""
    for prefix, action in (("记住：", "save"), ("记住:", "save"),
                           ("忘记：", "forget"), ("忘记:", "forget")):
        if text.startswith(prefix):
            return action, text[len(prefix):].strip()
    return None
