"""Local workspace product APIs: history, attachments and explicit review actions."""
from pathlib import Path
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from db import chat_memory, review
from db.database import get_connection

router = APIRouter()


@router.get('/api/agent/sessions')
def sessions(q: str = '', limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0)):
    items = chat_memory.list_sessions(q, limit+1, offset)
    return {'items':items[:limit], 'has_more': len(items)>limit}


class Title(BaseModel):
    title: str = Field(min_length=1, max_length=80)


@router.patch('/api/agent/sessions/{session_id}')
def rename(session_id: str, body: Title):
    try:
        chat_memory.rename_session(session_id, body.title)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {'ok':True}


@router.get('/api/agent/sessions/{session_id}/messages')
def messages(session_id: str, before_id: int | None = None, limit: int = Query(50, ge=1, le=100)):
    if not chat_memory.session_exists(session_id):
        raise HTTPException(404, '会话不存在')
    return chat_memory.message_page(session_id, before_id, limit)


@router.get('/api/attachments/{attachment_id}')
def attachment(attachment_id: str):
    conn = get_connection()
    try:
        row = conn.execute('SELECT * FROM chat_attachments WHERE id=?', (attachment_id,)).fetchone()
    finally:
        conn.close()
    if not row or not Path(row['path']).is_file():
        raise HTTPException(404, '图片不存在')
    return FileResponse(row['path'])


def _review_error(exc):
    return HTTPException(409 if isinstance(exc, review.VersionConflict) else
                         404 if isinstance(exc, LookupError) else 422, str(exc))


@router.get('/api/reviews/{doc_id}')
def get_review(doc_id: int):
    try:
        result = review.get_review(doc_id)
    except LookupError as exc:
        raise _review_error(exc) from exc
    conn = get_connection()
    try:
        images = conn.execute('SELECT id FROM chat_attachments WHERE path LIKE ? ORDER BY rowid DESC LIMIT 1',
                              ('%'+result['file_name'],)).fetchone()
        result['image_url'] = '/api/attachments/'+images['id'] if images else None
    finally:
        conn.close()
    return result


class Edit(BaseModel):
    id: int
    fields: dict


class ReviewAction(BaseModel):
    version: int = Field(ge=0)
    session_id: str | None = None


class BatchEdit(ReviewAction):
    edits: list[Edit] = Field(min_length=1, max_length=500)


def _check_session(session_id):
    if session_id:
        if not chat_memory.session_exists(session_id):
            raise HTTPException(404, '会话不存在')
        from web.agent_chat import _ACTIVE_SESSIONS, _SESSION_LOCK
        with _SESSION_LOCK:
            if session_id in _ACTIVE_SESSIONS:
                raise HTTPException(409, '请等待本会话当前任务完成，再核对数据')


def _save_activity(session_id, doc_id, user, answer):
    if session_id:
        chat_memory.save_turn(session_id, user, answer,
            assistant_meta={'cards':[{'type':'ocr_review','doc_id':doc_id}]})


@router.post('/api/reviews/{doc_id}/edits')
def edits(doc_id: int, body: BatchEdit):
    _check_session(body.session_id)
    try:
        result = review.save_edits(doc_id, body.version, [edit.model_dump() for edit in body.edits])
    except (ValueError, LookupError) as exc:
        raise _review_error(exc) from exc
    changes = '; '.join(f"行#{change['id']}："+', '.join(f"{key}: {change['before'][key]} → {value}"
        for key,value in change['after'].items()) for change in result['changes'])
    _save_activity(body.session_id, doc_id, '在核对表格中保存修改',
        ('修改已保存，修改行仍待确认。'+changes) if changes else '数据没有变化。')
    return result


@router.post('/api/reviews/{doc_id}/confirm')
def confirm(doc_id: int, body: ReviewAction):
    _check_session(body.session_id)
    try:
        result = review.confirm(doc_id, body.version)
    except (ValueError, LookupError) as exc:
        raise _review_error(exc) from exc
    _save_activity(body.session_id, doc_id, '核对无误，确认本单据',
                   f"已确认{result['changed']}行；单据 {result['file_name']} 的已确认行可进入统计。")
    return result


@router.get('/api/product/status')
def status():
    import config
    key = config.QWEN_OCR_API_KEY if config.OCR_PROVIDER=='qwen' else config.OCR_API_KEY
    return {'ocr_configured':bool(key) or config.OCR_PROVIDER=='internvl', 'ocr_provider': config.OCR_PROVIDER}
