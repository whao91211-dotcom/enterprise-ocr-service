"""Offline probe of conversation context available at the Agent HTTP boundary.

The Agent is replaced at the network/model boundary. This measures context
delivery, not answer quality, OCR accuracy, or model latency.
"""

from __future__ import annotations

import json
import argparse
import sys
import tempfile
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from db import database  # Keep the module alive across patch.dict's import cleanup.


def _observe(message: str, history: list[dict], image_path: str | None):
    observation = {
        "message": message,
        "history": history,
        "image_received": bool(image_path and Path(image_path).is_file()),
    }
    yield {"type": "answer", "answer": json.dumps(observation, ensure_ascii=False)}


def _response(client: TestClient, path: str, **kwargs) -> dict:
    response = client.post(path, **kwargs)
    response.raise_for_status()
    events = [json.loads(part.removeprefix("data: "))
              for part in response.text.strip().split("\n\n")]
    answer = next(event for event in events if event["type"] == "answer")
    observed = json.loads(answer["answer"])
    observed["session_id"] = next(event["session_id"] for event in events
                                  if event["type"] == "session")
    return observed


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--after", action="store_true",
                        help="Resume with the server-issued SQLite session ID")
    args = parser.parse_args()
    stub = ModuleType("agent.agent")
    stub.run_agent_events = _observe
    with patch.dict(sys.modules, {"agent.agent": stub}):
        from web import agent_chat

    agent_chat.run_agent_events = _observe
    app = FastAPI()
    app.include_router(agent_chat.router)

    with tempfile.TemporaryDirectory(prefix="agent_memory_baseline_") as tmp:
        database._DATA_DIR = Path(tmp)
        database.DB_PATH = Path(tmp) / "agent.db"
        database.init_db()
        agent_chat.UPLOAD_DIR = Path(tmp)
        client = TestClient(app)

        prior_text = "甲公司2024年的销售额是多少？"
        text_history = [{"role": "user", "content": prior_text}]
        text = _response(
            client,
            "/api/agent/chat",
            json={"message": "按商品分类", "history": text_history},
        )

        prior_image = "请上传单据图片"
        image_history = [{"role": "assistant", "content": prior_image}]
        image = _response(
            client,
            "/api/agent/chat/upload",
            data={"message": "识别这张单据", "history": json.dumps(image_history)},
            files={"file": ("synthetic.png", b"synthetic-image", "image/png")},
        )

        preference = "以后默认按月汇总"
        initial = _response(client, "/api/agent/chat",
                            json={"message": preference, "history": []})
        # Simulate a reload: no browser history is sent, only the saved session ID.
        reopened = TestClient(app)
        resume_body = {"message": "继续刚才的汇总", "history": []}
        if args.after:
            resume_body["session_id"] = initial["session_id"]
        resume = _response(
            reopened,
            "/api/agent/chat",
            json=resume_body,
        )

    cases = [
        {
            "case": "text_followup_with_client_history",
            "context_available": text["message"] == "按商品分类" and text["history"] == text_history,
        },
        {
            "case": "image_followup_with_client_history",
            "context_available": image["message"] == "识别这张单据"
            and image["history"] == image_history and image["image_received"],
        },
        {
            "case": "resume_after_page_reload",
            "context_available": any(
                preference in item.get("content", "") for item in resume["history"]
            ),
        },
    ]
    print(json.dumps({
        "metric": "required_context_available_at_agent_boundary",
        "mode": "sqlite_session_memory" if args.after else "browser_history_only",
        "offline_cases": len(cases),
        "available_cases": sum(case["context_available"] for case in cases),
        "cases": cases,
        "limitations": [
            "Agent and OCR are replaced; this is not answer accuracy or OCR accuracy",
            "No model latency, token usage, or API cost is measured",
        ],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
