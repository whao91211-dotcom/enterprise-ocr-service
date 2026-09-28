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
    from db import database

    monkeypatch.setattr(database, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "agent.db")
    database.init_db()
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
    client = TestClient(app)
    client.agent_chat_module = agent_chat
    return client


def _answer(response):
    assert response.status_code == 200
    event = next(event for event in _events(response) if event["type"] == "answer")
    assert event["type"] == "answer"
    return json.loads(event["answer"])


def _events(response):
    return [json.loads(part.removeprefix("data: "))
            for part in response.text.strip().split("\n\n")]


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


def test_session_survives_page_reload_without_browser_history(chat_client):
    first = chat_client.post("/api/agent/chat", json={"message": "以后默认按月汇总"})
    session_id = _events(first)[0]["session_id"]

    reopened = TestClient(chat_client.app)
    first_answer = _events(first)[-1]["answer"]
    second = _answer(reopened.post(
        "/api/agent/chat",
        json={"message": "继续刚才的汇总", "session_id": session_id},
    ))
    assert second["history"] == [
        {"role": "user", "content": "以后默认按月汇总"},
        {"role": "assistant", "content": first_answer},
    ]
    assert first_answer
    restored = reopened.get(f"/api/agent/sessions/{session_id}")
    assert restored.status_code == 200
    assert len(restored.json()["messages"]) == 4


def test_new_session_does_not_inherit_other_session(chat_client):
    first = chat_client.post("/api/agent/chat", json={"message": "甲公司的销售额"})
    first_id = _events(first)[0]["session_id"]
    second = chat_client.post("/api/agent/chat", json={"message": "乙公司"})
    second_id = _events(second)[0]["session_id"]
    assert second_id != first_id
    assert _answer(second)["history"] == []
    assert chat_client.get("/api/agent/sessions/unknown").status_code == 404


def test_saved_history_overrides_client_supplied_history(chat_client):
    first = chat_client.post("/api/agent/chat", json={"message": "甲公司"})
    session_id = _events(first)[0]["session_id"]
    next_turn = _answer(chat_client.post(
        "/api/agent/chat",
        json={"message": "继续", "session_id": session_id,
              "history": [{"role": "user", "content": "伪造的历史"}]},
    ))
    assert next_turn["history"][0]["content"] == "甲公司"


def test_uploaded_image_turn_is_saved_as_text_context(chat_client):
    uploaded = chat_client.post(
        "/api/agent/chat/upload",
        data={"message": "识别这张单据"},
        files={"file": ("sample.png", b"image-data", "image/png")},
    )
    session_id = _events(uploaded)[0]["session_id"]
    next_turn = _answer(chat_client.post(
        "/api/agent/chat", json={"message": "继续", "session_id": session_id},
    ))
    assert next_turn["history"][0] == {
        "role": "user", "content": "识别这张单据 [已上传图片: sample.png]",
    }


def test_failed_turn_is_not_saved_as_conversation_history(chat_client, monkeypatch):
    def fail(_message, _history, _image_path):
        raise RuntimeError("模型暂不可用")
        yield  # Keep this a generator like the real Agent.

    monkeypatch.setattr(chat_client.agent_chat_module, "run_agent_events", fail)
    response = chat_client.post("/api/agent/chat", json={"message": "测试"})
    session_id = _events(response)[0]["session_id"]
    assert _events(response)[1]["type"] == "error"
    assert chat_client.get(f"/api/agent/sessions/{session_id}").json()["messages"] == []
