"""SQLite schema: documents/ocr_rows plus chat sessions/messages.

- documents: 上传的每张图(支票/销售单)
  - id, file_name(源文件名, 唯一), sha256, uploaded_at
  - status: 图级状态(recognition_pending / rows_inserted)
- ocr_rows: 每张图识别出的行数据(8 列) + 人工确认状态
  - id, doc_id(FK->documents), seq(行号)
  - desc(顾客公司), date(发注日), from(源公司), item(项目),
    amount(数量), price(单价), tax(税率), sum(金额)
  - status: pending(待确认) / confirmed(已确认)
  - modified_by, modified_at, created_at
- chat_sessions/chat_messages: 会话 ID 和逐轮用户/Agent 消息
- memory_profiles/memory_preferences: 跨会话档案及用户明确保存的偏好
"""

SQL_SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS generated_artifacts (
    id TEXT PRIMARY KEY,
    path TEXT NOT NULL,
    kind TEXT NOT NULL,
    snapshot_json TEXT NOT NULL DEFAULT '{}',
    preview_status TEXT NOT NULL DEFAULT 'pending',
    preview_error TEXT NOT NULL DEFAULT '',
    preview_path TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS chat_attachments (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES chat_sessions(id),
    path TEXT NOT NULL,
    name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS documents (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    file_name   TEXT NOT NULL UNIQUE,
    sha256      TEXT,
    uploaded_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    status      TEXT NOT NULL DEFAULT 'recognition_pending'
);

CREATE TABLE IF NOT EXISTS ocr_rows (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id      INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    seq         INTEGER NOT NULL DEFAULT 0,
    desc_       TEXT,
    date_       TEXT,
    from_       TEXT,
    item        TEXT,
    amount      TEXT,
    price       TEXT,
    tax         TEXT,
    sum_        TEXT,
    status      TEXT NOT NULL DEFAULT 'pending',   -- pending / confirmed
    modified_by TEXT,
    modified_at TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE INDEX IF NOT EXISTS idx_ocr_rows_doc    ON ocr_rows(doc_id);
CREATE INDEX IF NOT EXISTS idx_ocr_rows_status ON ocr_rows(status);
CREATE INDEX IF NOT EXISTS idx_ocr_rows_item   ON ocr_rows(item);

CREATE TABLE IF NOT EXISTS chat_sessions (
    id         TEXT PRIMARY KEY,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS chat_messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
    role       TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content    TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE INDEX IF NOT EXISTS idx_chat_messages_session
    ON chat_messages(session_id, id);

CREATE TABLE IF NOT EXISTS chat_task_state (
    session_id TEXT PRIMARY KEY REFERENCES chat_sessions(id) ON DELETE CASCADE,
    content TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS memory_profiles (
    id         TEXT PRIMARY KEY,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS memory_preferences (
    profile_id TEXT NOT NULL REFERENCES memory_profiles(id) ON DELETE CASCADE,
    content    TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    PRIMARY KEY (profile_id, content)
);
"""
