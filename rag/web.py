"""企业文档处理智能体 Web 入口（FastAPI）。

- GET  /           简易问答网页
- POST /api/chat   对话接口（JSON: {"message": "...", "history": [...]}）
- POST /api/rebuild 重建 RAG 索引
- GET  /api/stats  RAG 索引/OCR 服务状态
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel, Field

from rag import retriever
from rag.agent import run_turn, run_turn_events
from rag.ocr_tool import OCR_SERVICE_URL

app = FastAPI(title="企业文档处理智能体", version="0.2.0")

UPLOAD_DIR = Path(__file__).resolve().parent / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

_PAGE = """<!doctype html>
<html lang="zh">
<head>
<meta charset="utf-8">
<title>企业文档处理智能体</title>
<style>
 body{font-family:system-ui,sans-serif;max-width:860px;margin:24px auto;padding:0 16px;background:#f7f8fa;color:#222}
 h1{font-size:20px} .card{background:#fff;border:1px solid #e3e6ea;border-radius:10px;padding:16px;margin:12px 0}
 .msg{white-space:pre-wrap;line-height:1.6} .user{color:#0b57d0;font-weight:600}
 .trace{color:#5a5a5a;font-size:12px;margin:2px 0;font-family:Consolas,monospace}
 .tool{color:#7a5c00;background:#fff6dc;border-radius:6px;padding:2px 8px;font-size:13px}
 #chat{height:46vh;overflow-y:auto;border:1px solid #d5d9e0;border-radius:8px;padding:12px;background:#fff}
 input[type=text]{width:78%;padding:10px;border:1px solid #ccc;border-radius:8px}
 .inbar{display:flex;gap:8px;align-items:center;margin-top:8px}
 #q{flex:1;padding:10px;border:1px solid #ccc;border-radius:8px}
 button{padding:10px 18px;border:none;background:#0b57d0;color:#fff;border-radius:8px;cursor:pointer}
 button.sec{background:#fff;color:#0b57d0;border:1px solid #0b57d0}
 .hint{color:#666;font-size:13px;margin:6px 0}
 #preview{max-height:110px;border-radius:8px;border:1px dashed #aaa;display:none;margin:6px 0}
 #upname{font-size:12px;color:#555;margin-left:6px}
 #file{display:none}
</style>
</head>
<body>
<h1>📄 企业文档处理智能体</h1>
<div class="hint">支持: ①上传图片识别销售单据(可先识别再确认入库) ②查询已确认销售数据(例:“SETソフトウェア株式会社 2024 年买了什么空调?”)</div>
<div id="chat"></div>
<div class="inbar">
 <input type="text" id="q" placeholder="输入问题… 或点击📎上传图片识别销售单据" autocomplete="off">
 <button type="button" class="sec" onclick="document.getElementById('file').click()">📎 上传图片</button>
 <button type="button" onclick="sendMsg()">发送</button>
 <input type="file" id="file" accept="image/png,image/jpeg,image/webp,image/bmp" onchange="onPick(event)">
</div>
<div>
 <img id="preview" alt="预览">
 <span id="upname"></span>
</div>
<div class="hint">上传图片后自动识别；也可直接文字提问(例：“SETソフトウェア株式会社 2024 年买了什么?”)</div>
<script>
let history = [];
let pickedFile = null;
function onPick(e){
  const f = e.target.files[0];
  if(!f) return;
  pickedFile = f;
  document.getElementById('upname').textContent = '📎 ' + f.name;
  const prev = document.getElementById('preview');
  prev.src = URL.createObjectURL(f);
  prev.style.display = 'block';
}
async function sendMsg(){
  const q = document.getElementById('q').value.trim();
  if(!q && !pickedFile) return false;
  const label = q || (pickedFile ? '上传图片识别' : '');
  if(q){ appendMsg('user', q); history.push({role:'user', content:q}); }
  if(pickedFile){ appendMsg('user', '📎 '+pickedFile.name); }
  history = history.slice(-20);
  document.getElementById('q').value='';
  const box = appendMsg('bot', '…');
  box.id = 'cur';
  try{
    let r;
    if(pickedFile){
      const fd = new FormData();
      fd.append('file', pickedFile);
      if(q) fd.append('message', q);
      r = await fetch('/api/chat/upload', {method:'POST', body: fd});
    }else{
      r = await fetch('/api/chat/stream', {method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({message:q, history})});
    }
    if(!r.ok){ let e={}; try{e=await r.json()}catch(_){}; throw new Error(e.detail||r.status); }
    const reader = r.body.getReader();
    const dec = new TextDecoder();
    let acc = '';
    let doneEv = null;
    while(true){
      const {value, done} = await reader.read();
      if(done) break;
      acc += dec.decode(value, {stream:true});
      const parts = acc.split('\n\n');
      acc = parts.pop();
      for(const part of parts){
        if(!part.startsWith('data: ')) continue;
        const ev = JSON.parse(part.slice(6));
        if(ev.type==='intent'){ addLine(box, '🧭 意图: '+ev.intent); }
        else if(ev.type==='tool_start'){ addLine(box, '🔧 调用工具 '+ev.name+'('+JSON.stringify(ev.args)+')…'); }
        else if(ev.type==='tool_result'){ addLine(box, '    ↳ '+String(ev.result).slice(0,220)); }
        else if(ev.type==='done'){ doneEv=ev.answer; }
        else if(ev.type==='error'){ addLine(box, '⚠️ '+ev.message); }
      }
    }
    if(doneEv!=null){ box.textContent = '🤖 ' + doneEv; updateHistory(doneEv); }
  }catch(e){
    const cur=document.getElementById('cur'); if(cur) cur.textContent='⚠️ 错误: '+e.message;
  }
  // 清理本次上传状态
  pickedFile = null;
  document.getElementById('file').value = '';
  document.getElementById('preview').style.display = 'none';
  document.getElementById('upname').textContent = '';
  return false;
}
function addLine(box, t){
  if(box.textContent==='…') box.textContent='';
  const d=document.createElement('div');
  d.className='trace'; d.textContent=t;
  box.appendChild(d);
  document.getElementById('chat').scrollTop=99999;
}
function updateHistory(answer){
  history.push({role:'assistant', content: answer});
  history = history.slice(-20);
}
function appendMsg(who, text){
  const d=document.createElement('div');
  d.className='msg '+(who==='user'?'user':'bot');
  d.textContent=(who==='user'?'🧑 ':'🤖 ')+text;
  document.getElementById('chat').appendChild(d);
  document.getElementById('chat').scrollTop=99999;
  return d;
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


@app.post("/api/chat/stream")
async def chat_stream(body: ChatIn):
    """SSE 流式：逐步推送 意图识别→工具调用→工具结果→最终回答。"""

    async def gen():
        try:
            for ev in run_turn_events(body.message, body.history):
                yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
        except RuntimeError as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)}, ensure_ascii=False)}\n\n"
        except Exception as e:  # noqa: BLE001
            yield f"data: {json.dumps({'type': 'error', 'message': f'处理失败: {e}'}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"}
    )


@app.post("/api/chat/upload")
async def chat_upload(file: UploadFile = File(...), message: str | None = None):
    """上传图片 → 保存到 rag/uploads → 智能体识别并流式返回。

    form 字段: file(图片), message(可选说明, 默认"请识别这张销售单据图片")。
    """
    ext = (file.filename or "image.png").rsplit(".", 1)[-1].lower()
    if ext not in ("png", "jpg", "jpeg", "webp", "bmp"):
        raise HTTPException(status_code=400, detail=f"不支持的图片类型: {ext}")
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="上传文件为空")
    saved = UPLOAD_DIR / f"{uuid.uuid4().hex}.{ext}"
    saved.write_bytes(data)

    # 把图片路径交给智能体（ocr_recognize Tool 读取该路径识别）
    prompt = message or "请识别这张销售单据图片，并列出其中的商品信息"
    full_msg = f"{prompt}\n[已上传图片，路径: {saved}]"

    async def gen():
        try:
            for ev in run_turn_events(full_msg, None):
                yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
        except RuntimeError as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)}, ensure_ascii=False)}\n\n"
        except Exception as e:  # noqa: BLE001
            yield f"data: {json.dumps({'type': 'error', 'message': f'处理失败: {e}'}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"}
    )


@app.post("/api/rebuild")
async def rebuild():
    n = retriever.touch()
    return {"ok": True, "docs": n}


@app.get("/api/stats")
async def stats():
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
