"""Agent 聊天流式端点(SSE): 供深色工业风界面调用。

POST /api/agent/chat          JSON: {message, session_id?, profile_id?, history?}
POST /api/agent/chat/upload   multipart: file, message, session_id?, profile_id?, history?
返回 SSE 事件流: session/intent/llm_text/tool_call/tool_result/answer/error
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, ValidationError

from agent.agent import run_agent_events
from agent.memory_commands import parse_memory_command
from db import chat_memory, preference_memory

router = APIRouter(tags=["agent-chat"])

_ROOT = Path(__file__).resolve().parents[1]
UPLOAD_DIR = _ROOT / "data" / "chatuploads"
ALLOWED_EXT = {"png", "jpg", "jpeg", "webp", "bmp"}


class ChatIn(BaseModel):
    message: str = Field(default="", max_length=4000)
    history: list[dict] = Field(default_factory=list)
    session_id: str | None = None
    profile_id: str | None = None


@router.post("/api/agent/chat")
async def agent_chat(body: ChatIn):
    return _event_response(body.message, body.history, None, body.session_id,
                           body.profile_id)


@router.get("/api/agent/sessions/{session_id}")
def get_chat_session(session_id: str):
    if not chat_memory.session_exists(session_id):
        raise HTTPException(404, "会话不存在")
    return {"session_id": session_id, "messages": chat_memory.load_messages(session_id)}


@router.post("/api/agent/chat/upload")
async def agent_chat_upload(
    file: UploadFile = File(...),
    message: str = Form(""),
    history: str = Form("[]"),
    session_id: str = Form(""),
    profile_id: str = Form(""),
):
    try:
        body = ChatIn(message=message, history=json.loads(history),
                      session_id=session_id, profile_id=profile_id)
    except (json.JSONDecodeError, ValidationError) as e:
        raise HTTPException(422, f"聊天内容格式错误: {e}") from e

    ext = (file.filename or "x.png").rsplit(".", 1)[-1].lower()
    if ext not in ALLOWED_EXT:
        raise HTTPException(400, f"不支持的图片类型: {ext}")
    data = await file.read()
    if not data:
        raise HTTPException(400, "空文件")
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    saved = UPLOAD_DIR / f"{uuid.uuid4().hex}.{ext}"
    saved.write_bytes(data)
    return _event_response(body.message, body.history, str(saved), body.session_id,
                           body.profile_id, file.filename or "图片")


def _event_response(message: str, history: list[dict], image_path: str | None,
                    requested_session_id: str | None, requested_profile_id: str | None,
                    image_name: str | None = None):
    session_id, existed = chat_memory.resolve_session(requested_session_id)
    profile_id = preference_memory.resolve_profile(requested_profile_id)
    context = chat_memory.load_messages(session_id, limit=10) if existed else history[-10:]
    user_content = message.strip()
    if image_path:
        user_content = f"{user_content} [已上传图片: {image_name}]".strip()

    async def gen():
        try:
            yield f"data: {json.dumps({'type': 'session', 'session_id': session_id, 'profile_id': profile_id})}\n\n"
            command = parse_memory_command(message) if image_path is None else None
            if command is not None:
                answer = _handle_memory_command(profile_id, *command)
                yield f"data: {json.dumps({'type': 'answer', 'answer': answer}, ensure_ascii=False)}\n\n"
                return
            preferences = preference_memory.list_preferences(profile_id)
            for ev in run_agent_events(message, context, image_path,
                                       preferences=preferences):
                if ev.get("type") == "answer":
                    chat_memory.save_turn(session_id, user_content, ev["answer"])
                yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
        except RuntimeError as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)}, ensure_ascii=False)}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': f'处理失败: {e}'}, ensure_ascii=False)}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache"})


def _handle_memory_command(profile_id: str, action: str, content: str) -> str:
    if action == "list":
        preferences = preference_memory.list_preferences(profile_id)
        if not preferences:
            return "暂无已保存的记忆。"
        entries = "\n".join(f"{i}. {item}" for i, item in enumerate(preferences, 1))
        return f"已保存的记忆：\n{entries}"
    if not content:
        return "请在冒号后写明内容，例如：记住：以后回答简洁些。"
    if len(content) > 200:
        return "单条记忆不能超过 200 字。"
    if action == "save":
        status = preference_memory.save_preference(profile_id, content)
        if status == "exists":
            return f"这条偏好已经记住：{content}"
        if status == "full":
            return "最多保存 20 条偏好，请先使用“忘记：原偏好”删除一条。"
        return f"已记住：{content}"
    if preference_memory.forget_preference(profile_id, content):
        return f"已忘记：{content}"
    return "没有找到完全相同的记忆；发送“查看记忆”可查看原文。"
