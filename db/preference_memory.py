"""Explicit cross-session preferences stored in SQLite."""

from __future__ import annotations

import uuid

from db.database import get_connection

MAX_PREFERENCES = 20


def resolve_profile(requested_id: str | None) -> str:
    conn = get_connection()
    try:
        if requested_id:
            row = conn.execute(
                "SELECT id FROM memory_profiles WHERE id = ?", (requested_id,)
            ).fetchone()
            if row:
                return row["id"]
        profile_id = uuid.uuid4().hex
        with conn:
            conn.execute("INSERT INTO memory_profiles (id) VALUES (?)", (profile_id,))
        return profile_id
    finally:
        conn.close()


def list_preferences(profile_id: str) -> list[str]:
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT content FROM memory_preferences WHERE profile_id = ? "
            "ORDER BY created_at, rowid", (profile_id,)
        ).fetchall()
        return [row["content"] for row in rows]
    finally:
        conn.close()


def save_preference(profile_id: str, content: str) -> str:
    """Return saved, exists or full; never silently replace another preference."""
    conn = get_connection()
    try:
        with conn:
            # Reserve the write transaction before reading the count/duplicate.
            # Concurrent sessions sharing a profile must not exceed the limit.
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute(
                "SELECT 1 FROM memory_preferences WHERE profile_id = ? AND content = ?",
                (profile_id, content),
            ).fetchone():
                return "exists"
            count = conn.execute(
                "SELECT COUNT(*) FROM memory_preferences WHERE profile_id = ?",
                (profile_id,),
            ).fetchone()[0]
            if count >= MAX_PREFERENCES:
                return "full"
            conn.execute(
                "INSERT INTO memory_preferences (profile_id, content) VALUES (?, ?)",
                (profile_id, content),
            )
        return "saved"
    finally:
        conn.close()


def forget_preference(profile_id: str, content: str) -> bool:
    conn = get_connection()
    try:
        with conn:
            cursor = conn.execute(
                "DELETE FROM memory_preferences WHERE profile_id = ? AND content = ?",
                (profile_id, content),
            )
        return cursor.rowcount > 0
    finally:
        conn.close()
