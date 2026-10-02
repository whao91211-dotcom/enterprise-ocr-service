"""One latest successful-query snapshot per session, separate from preferences."""
import json
from db.database import get_connection


def load(session_id):
    conn = get_connection()
    try:
        row = conn.execute('SELECT content FROM chat_task_state WHERE session_id = ?', (session_id,)).fetchone()
        return json.loads(row['content']) if row else {}
    finally:
        conn.close()


def save(session_id, state):
    conn = get_connection()
    try:
        with conn:
            conn.execute("INSERT INTO chat_task_state(session_id, content) VALUES (?, ?) "
                         "ON CONFLICT(session_id) DO UPDATE SET content=excluded.content, "
                         "updated_at=datetime('now','localtime')",
                         (session_id, json.dumps(state, ensure_ascii=False)))
    finally:
        conn.close()
