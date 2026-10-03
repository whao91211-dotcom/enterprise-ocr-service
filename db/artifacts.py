"""Opaque IDs for files produced by trusted application tools, never client paths."""
import json
from pathlib import Path
from uuid import uuid4
from db.database import get_connection


def register(path, kind, snapshot=None):
    path = Path(path).resolve()
    if kind not in ('docx','xlsx','pptx','png') or path.suffix.lstrip('.').lower()!=kind or not path.is_file():
        raise ValueError('生成文件不存在或类型不匹配')
    ident = uuid4().hex
    conn = get_connection()
    try:
        with conn:
            conn.execute('INSERT INTO generated_artifacts(id,path,kind,snapshot_json,preview_status) VALUES(?,?,?,?,?)',
                         (ident,str(path),kind,json.dumps(snapshot or {},ensure_ascii=False),
                          'ready' if kind in ('xlsx','png') else 'pending'))
    finally:
        conn.close()
    return ident


def get(ident):
    conn = get_connection()
    try:
        row = conn.execute('SELECT * FROM generated_artifacts WHERE id=?',(ident,)).fetchone()
    finally:
        conn.close()
    if not row:
        raise LookupError('文件不存在')
    result = dict(row)
    if not Path(result['path']).is_file():
        raise FileNotFoundError('原文件已被移走或删除')
    result['name'] = Path(result['path']).name
    result['snapshot'] = json.loads(result.pop('snapshot_json'))
    return result


def preview_state(ident, status, error='', path=None):
    conn = get_connection()
    try:
        with conn:
            conn.execute('UPDATE generated_artifacts SET preview_status=?,preview_error=?,preview_path=? WHERE id=?',
                         (status,error,path,ident))
    finally:
        conn.close()
