"""SQLite 数据库连接与初始化(标准库 sqlite3, 无外部 ORM)。"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from db.schema import SQL_SCHEMA

_PROJ = Path(__file__).resolve().parents[1]
_DATA_DIR = Path(os.environ.get("AGENT_DATA_DIR", _PROJ / "data"))
DB_PATH = _DATA_DIR / "agent.db"


def get_connection() -> sqlite3.Connection:
    """打开一个连接(WAL, 行工厂), 调用方负责 close/commit。"""
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    """建表(幂等)。"""
    conn = get_connection()
    try:
        conn.executescript(SQL_SCHEMA)
        conn.commit()
    finally:
        conn.close()


def db_path() -> str:
    return str(DB_PATH)
