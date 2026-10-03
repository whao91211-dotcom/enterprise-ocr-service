"""Short-term conversation history for one browser session."""

from __future__ import annotations

import uuid
import json

from db.database import get_connection


def resolve_session(requested_id: str | None) -> tuple[str, bool]:
    """Return an existing session, or create a new unguessable one."""
    conn = get_connection()
    try:
        if requested_id:
            row = conn.execute(
                "SELECT id FROM chat_sessions WHERE id = ?", (requested_id,)
            ).fetchone()
            if row:
                return row["id"], True
        session_id = uuid.uuid4().hex
        with conn:
            conn.execute("INSERT INTO chat_sessions (id) VALUES (?)", (session_id,))
        return session_id, False
    finally:
        conn.close()


def session_exists(session_id: str) -> bool:
    conn = get_connection()
    try:
        return conn.execute(
            "SELECT 1 FROM chat_sessions WHERE id = ?", (session_id,)
        ).fetchone() is not None
    finally:
        conn.close()


def load_messages(session_id: str, limit: int = 20) -> list[dict[str, str]]:
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT role, content FROM ("
            "SELECT id, role, content FROM chat_messages WHERE session_id = ? "
            "ORDER BY id DESC LIMIT ?) ORDER BY id",
            (session_id, limit),
        ).fetchall()
        return [{"role": row["role"], "content": row["content"]} for row in rows]
    finally:
        conn.close()


def save_turn(session_id: str, user_content: str, assistant_content: str,
              user_meta: dict | None = None, assistant_meta: dict | None = None) -> None:
    conn = get_connection()
    try:
        with conn:
            conn.executemany(
                "INSERT INTO chat_messages (session_id, role, content, meta_json) VALUES (?, ?, ?, ?)",
                [(session_id, "user", user_content, json.dumps(user_meta or {}, ensure_ascii=False)),
                 (session_id, "assistant", assistant_content, json.dumps(assistant_meta or {}, ensure_ascii=False))],
            )
            conn.execute(
                "UPDATE chat_sessions SET updated_at = datetime('now','localtime'), "
                "title=CASE WHEN title='' THEN ? ELSE title END WHERE id = ?",
                (user_content.strip()[:36] or '新对话', session_id),
            )
    finally:
        conn.close()


def list_sessions(query='', limit=50, offset=0):
    conn = get_connection()
    try:
        rows = conn.execute("SELECT s.id,s.title,s.created_at,s.updated_at,"
            "(SELECT COUNT(*) FROM chat_messages m WHERE m.session_id=s.id) message_count "
            "FROM chat_sessions s WHERE (?='' OR s.title LIKE ? OR EXISTS(SELECT 1 FROM chat_messages m "
            "WHERE m.session_id=s.id AND m.content LIKE ?)) AND EXISTS(SELECT 1 FROM chat_messages m WHERE m.session_id=s.id) "
            "ORDER BY s.updated_at DESC,s.rowid DESC LIMIT ? OFFSET ?",
            (query, '%'+query+'%', '%'+query+'%', limit, offset)).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def rename_session(session_id, title):
    title = title.strip()
    if not title or len(title)>80:
        raise ValueError('标题须为1至80字')
    conn = get_connection()
    try:
        with conn:
            if not conn.execute('UPDATE chat_sessions SET title=? WHERE id=?', (title, session_id)).rowcount:
                raise LookupError('会话不存在')
    finally:
        conn.close()


def message_page(session_id, before_id=None, limit=50):
    conn = get_connection()
    try:
        rows = conn.execute('SELECT * FROM chat_messages WHERE session_id=? AND (? IS NULL OR id<?) '
            'ORDER BY id DESC LIMIT ?', (session_id, before_id, before_id, limit+1)).fetchall()
        more = len(rows)>limit
        messages = [{**dict(row), 'meta': json.loads(row['meta_json'])} for row in reversed(rows[:limit])]
        for message in messages:
            message.pop('meta_json', None)
        return {'messages': messages, 'has_more': more}
    finally:
        conn.close()
