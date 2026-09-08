"""SQLite schema: documents(图) + ocr_rows(识别行) 两级表。

- documents: 上传的每张图(支票/销售单)
  - id, file_name(源文件名, 唯一), sha256, uploaded_at
  - status: 图级状态(recognition_pending / rows_inserted)
- ocr_rows: 每张图识别出的行数据(8 列) + 人工确认状态
  - id, doc_id(FK->documents), seq(行号)
  - desc(顾客公司), date(发注日), from(源公司), item(项目),
    amount(数量), price(单价), tax(税率), sum(金额)
  - status: pending(待确认) / confirmed(已确认)
  - modified_by, modified_at, created_at
"""

SQL_SCHEMA = """
PRAGMA journal_mode=WAL;

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
"""
