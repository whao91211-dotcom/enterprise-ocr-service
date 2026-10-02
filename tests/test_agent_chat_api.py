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

    def echo_input(message, history, image_path, preferences=None, task_state=None):
        observed = {
            "message": message,
            "history": history,
            "image_bytes": Path(image_path).read_bytes().decode("ascii") if image_path else None,
        }
        if preferences is not None:
            observed["preferences"] = preferences
        if task_state:
            observed['task_state'] = task_state
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


def test_same_session_busy_request_is_not_executed_or_saved(chat_client, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    seed = chat_client.post('/api/agent/chat', json={'message': 'seed'})
    sid = _events(seed)[0]['session_id']
    started, release = Event(), Event()
    observed = []

    def controlled(message, history, image_path, **kwargs):
        observed.append(message)
        if message == 'slow':
            started.set()
            assert release.wait(5)
        yield {'type': 'answer', 'answer': json.dumps({'history': history})}

    monkeypatch.setattr(chat_client.agent_chat_module, 'run_agent_events', controlled)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(TestClient(chat_client.app).post, '/api/agent/chat',
                             json={'message': 'slow', 'session_id': sid})
        try:
            assert started.wait(5)
            competing = chat_client.post('/api/agent/chat', json={'message': 'competing', 'session_id': sid})
            assert any(e['type'] == 'error' and e.get('code') == 'session_busy' for e in _events(competing))
        finally:
            release.set()
            future.result(timeout=5)
    assert observed == ['slow']
    messages = chat_client.get(f'/api/agent/sessions/{sid}').json()['messages']
    assert [m['content'] for m in messages if m['role'] == 'user'] == ['seed', 'slow']
    resumed = chat_client.post('/api/agent/chat', json={'message': 'after', 'session_id': sid})
    assert _answer(resumed)['history'] == messages


def test_query_state_is_persisted_and_forwarded_after_reload(chat_client, monkeypatch):
    state = {'tool': 'rag_summarize', 'year': '2024', 'keyword': '甲公司', 'group_by': 'desc'}
    def queried(message, history, image_path, preferences=None, task_state=None):
        if message == '首次查询':
            yield {'type': 'task_state', 'state': state}
        yield {'type': 'answer', 'answer': json.dumps(task_state or {})}
    monkeypatch.setattr(chat_client.agent_chat_module, 'run_agent_events', queried)
    first = chat_client.post('/api/agent/chat', json={'message': '首次查询'})
    session = _events(first)[0]['session_id']
    for _ in range(7):
        chat_client.post('/api/agent/chat', json={'message': '无关消息', 'session_id': session})
    resumed = chat_client.post('/api/agent/chat', json={'message': '继续最初查询', 'session_id': session})
    assert _answer(resumed) == state
    fresh = chat_client.post('/api/agent/chat', json={'message': '新查询'})
    assert _answer(fresh) == {}


def test_text_chat_forwards_message_and_history(chat_client):
    answer = _answer(chat_client.post(
        "/api/agent/chat",
        json={"message": "查2024年销售额", "history": [{"role": "user", "content": "甲公司"}]},
    ))
    assert answer == {
        "message": "查2024年销售额",
        "history": [{"role": "user", "content": "甲公司"}],
        "image_bytes": None,
        "preferences": [],
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
        "preferences": [],
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
    def fail(_message, _history, _image_path, preferences=None, task_state=None):
        raise RuntimeError("模型暂不可用")
        yield  # Keep this a generator like the real Agent.

    monkeypatch.setattr(chat_client.agent_chat_module, "run_agent_events", fail)
    response = chat_client.post("/api/agent/chat", json={"message": "测试"})
    session_id = _events(response)[0]["session_id"]
    assert _events(response)[1]["type"] == "error"
    assert chat_client.get(f"/api/agent/sessions/{session_id}").json()["messages"] == []


def test_explicit_preference_survives_new_conversation(chat_client):
    saved = chat_client.post("/api/agent/chat", json={"message": "记住：以后回答简洁些"})
    event = _events(saved)[0]
    profile_id = event["profile_id"]
    assert "已记住" in _events(saved)[-1]["answer"]

    reopened = TestClient(chat_client.app)
    next_chat = _answer(reopened.post(
        "/api/agent/chat", json={"message": "你好", "profile_id": profile_id},
    ))
    assert next_chat["preferences"] == ["以后回答简洁些"]
    assert next_chat["history"] == []


def test_memory_command_does_not_need_the_model(chat_client, monkeypatch):
    def fail(*_args, **_kwargs):
        raise AssertionError("memory command must not invoke Agent")

    monkeypatch.setattr(chat_client.agent_chat_module, "run_agent_events", fail)
    response = chat_client.post("/api/agent/chat", json={"message": "记住：语气正式"})
    assert "已记住" in _events(response)[-1]["answer"]


def test_ordinary_message_does_not_create_preference(chat_client):
    first = chat_client.post("/api/agent/chat", json={"message": "以后回答简洁些"})
    profile_id = _events(first)[0]["profile_id"]
    next_chat = _answer(chat_client.post(
        "/api/agent/chat", json={"message": "你好", "profile_id": profile_id},
    ))
    assert next_chat["preferences"] == []


def test_view_and_forget_preference(chat_client):
    saved = chat_client.post("/api/agent/chat", json={"message": "记住：语气正式"})
    profile_id = _events(saved)[0]["profile_id"]
    listing = chat_client.post("/api/agent/chat", json={"message": "查看记忆", "profile_id": profile_id})
    assert "语气正式" in _events(listing)[-1]["answer"]
    removed = chat_client.post(
        "/api/agent/chat", json={"message": "忘记：语气正式", "profile_id": profile_id},
    )
    assert "已忘记" in _events(removed)[-1]["answer"]
    next_chat = _answer(chat_client.post(
        "/api/agent/chat", json={"message": "你好", "profile_id": profile_id},
    ))
    assert next_chat["preferences"] == []


def test_preferences_are_isolated_by_profile(chat_client):
    saved = chat_client.post("/api/agent/chat", json={"message": "记住：按月汇总"})
    other = _answer(chat_client.post("/api/agent/chat", json={"message": "你好"}))
    assert other["preferences"] == []
    assert _events(saved)[0]["profile_id"]
