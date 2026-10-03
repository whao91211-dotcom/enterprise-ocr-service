"""Shared atomic business edits for chat tools and interactive review cards."""
from decimal import Decimal, InvalidOperation
from db.database import get_connection
from db import crud


class VersionConflict(ValueError):
    pass


def validate_fields(fields):
    if not isinstance(fields, dict) or not fields or set(fields)-set(crud.USER_FIELDS):
        raise ValueError('只允许修改单据的八个业务字段')
    result = {}
    for key, value in fields.items():
        if not isinstance(value, (str, int, float)) or isinstance(value, bool):
            raise ValueError('字段须为文本或数字')
        value = str(value).strip()
        if len(value) > 2000:
            raise ValueError('单个字段不能超过2000字')
        if key in ('amount', 'price', 'tax', 'sum') and value:
            try:
                number = Decimal(value.replace(',', '').replace('¥', '').replace('￥', '').rstrip('%'))
                if not number.is_finite():
                    raise InvalidOperation
            except InvalidOperation as exc:
                raise ValueError(f'{key} 必须是数字，可以为空或负数') from exc
        result[key] = value
    return result


def get_review(doc_id):
    conn = get_connection()
    try:
        doc = conn.execute('SELECT * FROM documents WHERE id=?', (doc_id,)).fetchone()
        if not doc:
            raise LookupError('单据不存在')
        rows = conn.execute('SELECT * FROM ocr_rows WHERE doc_id=? ORDER BY seq,id', (doc_id,)).fetchall()
        return {**dict(doc), 'rows': [crud._user_row(dict(row)) for row in rows]}
    finally:
        conn.close()


def _document(conn, doc_id, expected_version):
    doc = conn.execute('SELECT * FROM documents WHERE id=?', (doc_id,)).fetchone()
    if not doc:
        raise LookupError('单据不存在')
    if expected_version is not None and doc['version'] != expected_version:
        raise VersionConflict('单据已被更新，请刷新后重新核对')
    return doc


def save_edits(doc_id, expected_version, edits, reviewer='manual'):
    if not edits or len(edits) > 500:
        raise ValueError('请选择1至500行修改')
    normalized = [(edit['id'], validate_fields(edit['fields'])) for edit in edits]
    if len({row_id for row_id, _ in normalized}) != len(normalized):
        raise ValueError('同一行不能重复提交')
    conn = get_connection()
    changes = []
    try:
        with conn:
            conn.execute('BEGIN IMMEDIATE')
            _document(conn, doc_id, expected_version)
            for row_id, fields in normalized:
                row = conn.execute('SELECT * FROM ocr_rows WHERE id=? AND doc_id=?', (row_id, doc_id)).fetchone()
                if not row:
                    raise LookupError('修改行不存在或不属于本单据')
                before = crud._user_row(dict(row))
                changed = {k: v for k, v in fields.items() if str(before.get(k) or '') != v}
                if not changed:
                    continue
                columns = [crud._USER_TO_DB.get(k, k) for k in changed]
                conn.execute('UPDATE ocr_rows SET '+','.join(f'{key}=?' for key in columns)+
                    ",status='pending',modified_by=?,modified_at=datetime('now','localtime') WHERE id=?",
                    (*changed.values(), reviewer, row_id))
                changes.append({'id': row_id, 'before': {k: before.get(k) for k in changed}, 'after': changed})
            if changes:
                conn.execute('UPDATE documents SET version=version+1 WHERE id=?', (doc_id,))
        return {**get_review(doc_id), 'changes': changes}
    finally:
        conn.close()


def confirm(doc_id, expected_version=None, reviewer='manual'):
    conn = get_connection()
    try:
        with conn:
            conn.execute('BEGIN IMMEDIATE')
            _document(conn, doc_id, expected_version)
            count = conn.execute("UPDATE ocr_rows SET status='confirmed',modified_by=?,"
                "modified_at=datetime('now','localtime') WHERE doc_id=? AND status='pending'", (reviewer, doc_id)).rowcount
            if count:
                conn.execute('UPDATE documents SET version=version+1 WHERE id=?', (doc_id,))
        return {**get_review(doc_id), 'changed': count}
    finally:
        conn.close()
