"""FastAPI Web: 统一入口。

页面集成了整条链路:
  上传图片 → OCR 识别 → 查看/人工修改 → 确认 → RAG 问答 → 画图
API:
  GET  /                    单页界面
  POST /api/ocr             multipart 上传图片, 同步识别(先不经过 Agent, 快)
  GET  /api/docs            列出单据(含行数/确认状态)
  GET  /api/docs/{name}     某图识别行
  POST /api/rows/{id}       修改某行(人工)
  POST /api/confirm         confirm {file_name, reviewer}
  GET  /api/confirmed       已确认行(供展示/前端统计)
  POST /api/agent           文字问答(经 LangChain Agent)
  POST /api/chart           画图 {group_by, chart_type, filter}
  GET  /charts/{file}       图表文件
  GET  /api/stats           总览
"""

from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field

from db import crud
from db.database import init_db
from tools.ocr_client import OcrError, recognize
from web import agent_chat

ROOT = Path(__file__).resolve().parents[1]
UPLOAD_DIR = ROOT / "data" / "uploads"
CHART_DIR = ROOT / "charts"

app = FastAPI(title="企业文档处理智能体 v2", version="2.0.0")

# Agent 聊天流式端到端(自主多 Tool)
app.include_router(agent_chat.router)


@app.get("/", response_class=HTMLResponse)
async def index():
    """主界面: 深色工业风 Agent 聊天页。"""
    p = ROOT / "web" / "agentchat.html"
    if not p.is_file():
        return "AgentChat 页面未部署"
    return p.read_text(encoding="utf-8")


ALLOWED_EXT = {"png", "jpg", "jpeg", "webp", "bmp"}


@app.on_event("startup")
def _startup() -> None:
    init_db()
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    CHART_DIR.mkdir(parents=True, exist_ok=True)


# ---------- Pydantic ----------


class AgentIn(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    history: list[dict] = Field(default_factory=list)


class RowUpdateIn(BaseModel):
    fields: dict
    reviewer: str = "manual"


class ConfirmIn(BaseModel):
    file_name: str
    reviewer: str = "manual"


class ChartIn(BaseModel):
    group_by: str = "item"
    chart_type: str = "bar"
    filter: str = ""





# ---------- OCR ----------


@app.post("/api/ocr")
async def ocr_upload(file: UploadFile = File(...)):
    ext = (file.filename or "x.png").rsplit(".", 1)[-1].lower()
    if ext not in ALLOWED_EXT:
        raise HTTPException(400, f"不支持的图片类型: {ext}")
    data = await file.read()
    if not data:
        raise HTTPException(400, "空文件")
    saved = UPLOAD_DIR / f"{uuid.uuid4().hex}.{ext}"
    saved.write_bytes(data)
    try:
        result = recognize(data, ext)
    except OcrError as e:
        raise HTTPException(422, str(e)) from e
    rows = result["rows"]
    if not rows:
        raise HTTPException(422, f"识别到 0 行: {result['raw'][:150]}")
    file_name = file.filename or saved.name
    doc_id = crud.upsert_document(file_name, None)
    crud.delete_rows_by_doc(doc_id)
    crud.insert_rows(doc_id, rows)
    return {
        "summary": f"{file_name}: {len(rows)} 行(待确认), {result['latency_ms']}ms",
        "rows": rows,
        "doc_id": doc_id,
    }


# ---------- Correct ----------


@app.get("/api/docs")
async def list_docs():
    return {"items": crud.all_docs_with_stats()}


@app.get("/api/docs/{name}")
async def doc_rows(name: str):
    doc = crud.get_document_by_name(name)
    if not doc:
        raise HTTPException(404, f"未找到 {name}")
    return {"file_name": name, "rows": crud.rows_by_doc(doc["id"])}


@app.post("/api/rows/{row_id}")
async def update_row(row_id: int, body: RowUpdateIn):
    ok = crud.update_row(row_id, body.fields, body.reviewer)
    if not ok:
        raise HTTPException(400, "更新失败(字段非法或行不存在)")
    return {"ok": True, "id": row_id}


@app.post("/api/confirm")
async def confirm_doc(body: ConfirmIn):
    doc = crud.get_document_by_name(body.file_name)
    if not doc:
        raise HTTPException(404, f"未找到 {body.file_name}")
    n = crud.confirm_doc(doc["id"], body.reviewer)
    return {"ok": True, "message": f"{body.file_name} 的 {n} 行已确认"}


@app.get("/api/confirmed")
async def confirmed():
    return {"items": crud.confirmed_rows()}


# ---------- Agent / Chart ----------


@app.post("/api/agent")
async def agent_chat(body: AgentIn):
    from agent.agent import ask as agent_ask

    try:
        answer = agent_ask(body.message, body.history)
    except RuntimeError as e:
        raise HTTPException(500, str(e)) from e
    history = list(body.history) + [
        {"role": "user", "content": body.message},
        {"role": "assistant", "content": answer},
    ]
    return {"answer": answer, "history": history[-20:], "chart": None}


@app.post("/api/chart")
async def make_chart(body: ChartIn):

    try:
        from tools.plot_tool import plot_chart

        msg = plot_chart.invoke({"group_by": body.group_by, "chart_type": body.chart_type,
                                 "filter_query": body.filter})
    except Exception as e:  # noqa: BLE001
        raise HTTPException(422, str(e)) from e
    # plot_chart 返回 "图表已生成: <path> (...)"
    path = msg.split(": ")[1].split(" (")[0] if ": " in msg else ""
    from pathlib import Path

    return {"message": msg, "file": Path(path).name if path else None}


@app.get("/charts/{file}")
async def get_chart(file: str):
    p = (CHART_DIR / file).resolve()
    if not p.is_file() or p.parent != CHART_DIR:
        raise HTTPException(404, "图表不存在")
    return FileResponse(p, media_type="image/png")


@app.get("/api/stats")
async def stats():
    return crud.stats()
