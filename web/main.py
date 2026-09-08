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

ROOT = Path(__file__).resolve().parents[1]
UPLOAD_DIR = ROOT / "data" / "uploads"
CHART_DIR = ROOT / "charts"

app = FastAPI(title="企业文档处理智能体 v2", version="2.0.0")

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


# ---------- 页面 ----------

_PAGE = """<!doctype html>
<html lang="zh"><head><meta charset="utf-8">
<title>企业文档处理智能体 v2</title>
<style>
 body{font-family:system-ui;max-width:960px;margin:20px auto;padding:0 16px;background:#f6f8fa;color:#222}
 h1{font-size:22px} h2{font-size:16px;margin:14px 0 6px}
 .card{background:#fff;border:1px solid #e0e4e8;border-radius:10px;padding:14px;margin:10px 0}
 .row{display:flex;gap:10px;flex-wrap:wrap;align-items:center}
 button{padding:8px 14px;border:none;border-radius:8px;background:#0b57d0;color:#fff;cursor:pointer}
 button.gray{background:#eee;color:#222} input,select{padding:7px;border:1px solid #ccc;border-radius:6px}
 table{border-collapse:collapse;width:100%;font-size:13px}
 th,td{border:1px solid #e2e5e9;padding:4px 6px;text-align:left}
 th{background:#f0f2f5} td input{width:90px;padding:2px}
 .ok{color:#1a7f37} .warn{color:#b35900} .msg{white-space:pre-wrap;line-height:1.5;font-size:14px}
 #chat{height:220px;overflow:auto;border:1px solid #ddd;border-radius:8px;padding:10px;background:#fff}
 img{max-width:220px;border:1px solid #ddd;border-radius:6px}
</style></head><body>
<h1>📄 企业文档处理智能体 v2</h1>

<div class="card">
 <h2>1. 上传单据图片 → OCR 识别</h2>
 <div class="row">
  <input type="file" id="file" accept="image/png,image/jpeg,image/webp,image/bmp">
  <button onclick="upload()">识别此图</button>
  <span id="upstat"></span>
 </div>
 <div id="ocrmsg" class="msg"></div>
</div>

<div class="card">
 <h2>2. 识别数据核对 / 人工修改（Correct）</h2>
 <div class="row">
  <button class="gray" onclick="loadDocs()">刷新单据列表</button>
  <select id="docsel" onchange="loadDocRows()"></select>
  <button onclick="confirmDoc()">✅ 确认此单据全部行</button>
 </div>
 <div style="overflow:auto;max-height:280px"><table id="rowtable"></table></div>
 <div id="cmsg" class="msg"></div>
</div>

<div class="card">
 <h2>3. 智能体问答（RAG / 画图）</h2>
 <div id="chat"></div>
 <div class="row" style="margin-top:6px">
  <input id="q" style="flex:1" placeholder="例：统计每类商品的金额并画柱状图">
  <button onclick="askAgent()">发送</button>
 </div>
</div>

<script>
async function jpost(url, body){ const r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}); const j=await r.json(); if(!r.ok) throw new Error(j.detail||r.status); return j; }
function setMsg(id,t){ const el=document.getElementById(id); el.textContent=t; }

async function upload(){
 const f=document.getElementById('file').files[0]; if(!f) return;
 setMsg('upstat','上传中…'); setMsg('ocrmsg','');
 const fd=new FormData(); fd.append('file',f);
 const r=await fetch('/api/ocr',{method:'POST',body:fd}); const j=await r.json();
 if(!r.ok){ setMsg('ocrmsg','⚠️ '+j.detail); return; }
 let s='✅ '+j.summary;
 (j.rows||[]).forEach((row,i)=>{ s+='\\n'+(i+1)+'. '+(row.desc||'')+' '+(row.date||'')+' '+(row.from||'')+' '+row.item+' ×'+row.amount+' 单价'+row.price+' 金额'+row.sum; });
 setMsg('ocrmsg',s); setMsg('upstat','完成(入库待确认)'); loadDocs();
}
async function loadDocs(){
 const r=await fetch('/api/docs'); const j=await r.json();
 const sel=document.getElementById('docsel'); sel.innerHTML='';
 j.items.forEach(d=>{ const o=document.createElement('option'); o.value=d.file_name; o.textContent=`${d.file_name} (${d.row_count}行/确认${d.confirmed_count})`; sel.appendChild(o); });
 if(j.items.length) loadDocRows();
}
async function loadDocRows(){
 const name=document.getElementById('docsel').value; if(!name) return;
 const r=await fetch('/api/docs/'+encodeURIComponent(name)); const j=await r.json();
 const tb=document.getElementById('rowtable');
 let html='<tr><th>id</th><th>状态</th><th>顾客</th><th>日期</th><th>源公司</th><th>项目</th><th>数量</th><th>单价</th><th>税率</th><th>金额</th><th></th></tr>';
 (j.rows||[]).forEach(row=>{
   html+=`<tr><td>${row.id}</td><td>${row.status==='confirmed'?'✅':'⏳'}</td>
    <td><input id="f_${row.id}_desc" value="${row.desc||''}"></td>
    <td><input id="f_${row.id}_date" value="${row.date||''}"></td>
    <td><input id="f_${row.id}_from" value="${row.from||''}"></td>
    <td><input id="f_${row.id}_item" value="${row.item||''}"></td>
    <td><input id="f_${row.id}_amount" value="${row.amount||''}"></td>
    <td><input id="f_${row.id}_price" value="${row.price||''}"></td>
    <td><input id="f_${row.id}_tax" value="${row.tax||''}"></td>
    <td><input id="f_${row.id}_sum" value="${row.sum||''}"></td>
    <td><button class="gray" onclick="saveRow(${row.id})">保存</button></td></tr>`;
 });
 tb.innerHTML=html;
}
async function saveRow(id){
 const fields={}; ['desc','date','from','item','amount','price','tax','sum'].forEach(k=>{ const el=document.getElementById(`f_${id}_${k}`); if(el) fields[k]=el.value; });
 try{ const j=await jpost('/api/rows/'+id,{fields,reviewer:'manual'}); setMsg('cmsg','✅ 行已保存'); loadDocRows(); }
 catch(e){ setMsg('cmsg','⚠️ '+e.message); }
}
async function confirmDoc(){
 const name=document.getElementById('docsel').value; if(!name) return;
 try{ const j=await jpost('/api/confirm',{file_name:name,reviewer:'manual'}); setMsg('cmsg',j.message); loadDocs(); }
 catch(e){ setMsg('cmsg','⚠️ '+e.message); }
}
let history=[];
async function askAgent(){
 const q=document.getElementById('q').value.trim(); if(!q) return;
 appendChat('🧑 '+q); document.getElementById('q').value='';
 appendChat('🤖 …');
 try{
   const r=await fetch('/api/agent',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message:q,history})});
   const j=await r.json(); if(!r.ok) throw new Error(j.detail||r.status);
   history=j.history;
   const box=document.getElementById('chat'); box.lastChild.textContent='🤖 '+j.answer;
   const fig=j.chart; if(fig){ const img=document.createElement('img'); img.src='/charts/'+fig; box.appendChild(img); }
 }catch(e){ const box=document.getElementById('chat'); box.lastChild.textContent='⚠️ '+e.message; }
}
function appendChat(t){ const d=document.createElement('div'); d.className='msg'; d.textContent=t; document.getElementById('chat').appendChild(d); }
loadDocs();
</script></body></html>"""


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    return _PAGE


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
