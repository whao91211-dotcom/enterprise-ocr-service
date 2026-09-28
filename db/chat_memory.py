"""Short-term conversation history for one browser session."""

from __future__ import annotations

import uuid

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


def save_turn(session_id: str, user_content: str, assistant_content: str) -> None:
    conn = get_connection()
    try:
        with conn:
            conn.executemany(
                "INSERT INTO chat_messages (session_id, role, content) VALUES (?, ?, ?)",
                [(session_id, "user", user_content),
                 (session_id, "assistant", assistant_content)],
            )
            conn.execute(
                "UPDATE chat_sessions SET updated_at = datetime('now','localtime') WHERE id = ?",
                (session_id,),
            )
    finally:
        conn.close()
