"""Agent 聊天流式端点(SSE): 供深色工业风界面调用。

POST /api/agent/chat   JSON: {message, history} 或 multipart(file图片)
返回 SSE 事件流: intent/llm_text/tool_call/tool_result/answer/error
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from agent.agent import run_agent_events

router = APIRouter(tags=["agent-chat"])

_ROOT = Path(__file__).resolve().parents[1]
UPLOAD_DIR = _ROOT / "data" / "chatuploads"
ALLOWED_EXT = {"png", "jpg", "jpeg", "webp", "bmp"}


class ChatIn(BaseModel):
    message: str = Field(default="", max_length=4000)
    history: list[dict] = Field(default_factory=list)


@router.post("/api/agent/chat")
async def agent_chat(body: ChatIn | None = None, file: UploadFile | None = None):
    message = body.message if body else ""
    history = body.history if body else []

    image_path = None
    if file is not None:
        ext = (file.filename or "x.png").rsplit(".", 1)[-1].lower()
        if ext not in ALLOWED_EXT:
            raise HTTPException(400, f"不支持的图片类型: {ext}")
        data = await file.read()
        if not data:
            raise HTTPException(400, "空文件")
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        saved = UPLOAD_DIR / f"{uuid.uuid4().hex}.{ext}"
        saved.write_bytes(data)
        image_path = str(saved)

    async def gen():
        try:
            for ev in run_agent_events(message, history, image_path):
                yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
        except RuntimeError as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)}, ensure_ascii=False)}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': f'处理失败: {e}'}, ensure_ascii=False)}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache"})
