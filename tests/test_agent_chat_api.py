"""HTTP contract tests for chat; the LLM and OCR service are replaced at the boundary."""

import json
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def chat_client(monkeypatch, tmp_path):
    stub = ModuleType("agent.agent")
    stub.run_agent_events = lambda *_args: iter(())
    with patch.dict(sys.modules, {"agent.agent": stub}):
        from web import agent_chat

    monkeypatch.setattr(agent_chat, "UPLOAD_DIR", tmp_path)

    def echo_input(message, history, image_path):
        observed = {
            "message": message,
            "history": history,
            "image_bytes": Path(image_path).read_bytes().decode("ascii") if image_path else None,
        }
        yield {"type": "answer", "answer": json.dumps(observed, ensure_ascii=False)}

    monkeypatch.setattr(agent_chat, "run_agent_events", echo_input)
    app = FastAPI()
    app.include_router(agent_chat.router)
    return TestClient(app)


def _answer(response):
    assert response.status_code == 200
    event = json.loads(response.text.removeprefix("data: ").strip())
    assert event["type"] == "answer"
    return json.loads(event["answer"])


def test_text_chat_forwards_message_and_history(chat_client):
    answer = _answer(chat_client.post(
        "/api/agent/chat",
        json={"message": "查2024年销售额", "history": [{"role": "user", "content": "甲公司"}]},
    ))
    assert answer == {
        "message": "查2024年销售额",
        "history": [{"role": "user", "content": "甲公司"}],
        "image_bytes": None,
    }


def test_image_chat_forwards_file_message_and_history(chat_client):
    answer = _answer(chat_client.post(
        "/api/agent/chat/upload",
        data={
            "message": "识别这张单据",
            "history": json.dumps([{"role": "assistant", "content": "请上传图片"}]),
        },
        files={"file": ("sample.png", b"image-data", "image/png")},
    ))
    assert answer == {
        "message": "识别这张单据",
        "history": [{"role": "assistant", "content": "请上传图片"}],
        "image_bytes": "image-data",
    }
