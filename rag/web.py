"""企业文档处理智能体 Web 入口（FastAPI）。

- GET  /           简易问答网页
- POST /api/chat   对话接口（JSON: {"message": "...", "history": [...]}）
- POST /api/rebuild 重建 RAG 索引
- GET  /api/stats  RAG 索引/OCR 服务状态
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from rag import retriever
from rag.agent import run_turn
from rag.ocr_tool import OCR_SERVICE_URL

app = FastAPI(title="企业文档处理智能体", version="0.1.0")

_PAGE = """<!doctype html>
<html lang="zh">
<head>
<meta charset="utf-8">
<title>企业文档处理智能体</title>
<style>
 body{font-family:system-ui,sans-serif;max-width:860px;margin:24px auto;padding:0 16px;background:#f7f8fa;color:#222}
 h1{font-size:20px} .card{background:#fff;border:1px solid #e3e6ea;border-radius:10px;padding:16px;margin:12px 0}
 .msg{white-space:pre-wrap;line-height:1.6} .user{color:#0b57d0;font-weight:600}
 .tool{color:#7a5c00;background:#fff6dc;border-radius:6px;padding:2px 8px;font-size:13px}
 #chat{height:46vh;overflow-y:auto;border:1px solid #d5d9e0;border-radius:8px;padding:12px;background:#fff}
 input[type=text]{width:78%;padding:10px;border:1px solid #ccc;border-radius:8px}
 button{padding:10px 18px;border:none;background:#0b57d0;color:#fff;border-radius:8px;cursor:pointer}
 .hint{color:#666;font-size:13px;margin:6px 0}
 .history{display:none}
</style>
</head>
<body>
<h1>📄 企业文档处理智能体</h1>
<div class="hint">支持: ①上传图片识别销售单据(可先识别再确认入库) ②查询已确认销售数据(例:“SETソフトウェア株式会社 2024 年买了什么空调?”)</div>
<div id="chat"></div>
<form id="f" onsubmit="return sendMsg()">
 <input type="text" id="q" placeholder="输入问题… 例如: 帮我查一下哪张图里有 掃除機？" autocomplete="off">
 <button type="submit">发送</button>
</form>
<div class="hint">图片识别请在下方输入本机图片绝对路径，例如: C:\\Users\\wuhao\\Documents\\xxx\\Sample100.png</div>
<script>
let history = [];
async function sendMsg(){
  const q = document.getElementById('q').value.trim();
  if(!q) return false;
  appendMsg('user', q);
  document.getElementById('q').value='';
  try{
    const r = await fetch('/api/chat', {method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({message:q, history})});
    const j = await r.json();
    if(!r.ok) throw new Error(j.detail||r.status);
    history = j.history || [];
    appendMsg('bot', j.answer);
  }catch(e){ appendMsg('bot', '⚠️ 错误: '+e.message); }
  return false;
}
function appendMsg(who, text){
  const d=document.createElement('div');
  d.className='msg '+(who==='user'?'user':'bot');
  d.textContent=(who==='user'?'🧑 ':'🤖 ')+text;
  document.getElementById('chat').appendChild(d);
  document.getElementById('chat').scrollTop=99999;
}
</script>
</body>
</html>"""


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    history: list[dict[str, Any]] = Field(default_factory=list)


class ChatOut(BaseModel):
    answer: str
    history: list[dict[str, Any]]


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    return _PAGE


@app.post("/api/chat", response_model=ChatOut)
async def chat(body: ChatIn):
    try:
        answer = run_turn(body.message, body.history)
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"智能体处理失败: {e}") from e
    history = list(body.history)
    history.append({"role": "user", "content": body.message})
    history.append({"role": "assistant", "content": answer})
    history = history[-20:]
    return ChatOut(answer=answer, history=history)


@app.post("/api/rebuild")
async def rebuild():
    n = retriever.touch()
    return {"ok": True, "docs": n}


@app.get("/api/stats")
async def stats():
    from rag.ocr_tool import OCR_SERVICE_URL
    import httpx

    ocr_up = False
    try:
        with httpx.Client(timeout=3) as c:
            ocr_up = c.get(f"{OCR_SERVICE_URL}/healthz").status_code == 200
    except Exception:  # noqa: BLE001
        ocr_up = False
    idx = retriever.stats()
    return {"rag": idx, "ocr_service": {"url": OCR_SERVICE_URL, "up": ocr_up}}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app_agent:app", host="127.0.0.1", port=8100, reload=True)
