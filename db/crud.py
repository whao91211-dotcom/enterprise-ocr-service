"""documents / ocr_rows 的数据操作(CRUD)。

对外函数都自开连接并提交，方便被 Tool / Web 直接调用。
"""

from __future__ import annotations

from typing import Any

from db.database import get_connection

ROW_FIELDS = ["desc_", "date_", "from_", "item", "amount", "price", "tax", "sum_"]
# 对外展示键(去掉下划线): desc/date/from/sum
USER_FIELDS = ["desc", "date", "from", "item", "amount", "price", "tax", "sum"]
_DB_TO_USER = {"desc_": "desc", "date_": "date", "from_": "from", "sum_": "sum"}
_USER_TO_DB = {v: k for k, v in _DB_TO_USER.items()}


def _user_row(row: dict[str, Any]) -> dict[str, Any]:
    """数据库行 → 用户友好行(desc/date/from/sum 无下划线)。"""
    out: dict[str, Any] = {}
    for k, v in row.items():
        out[_DB_TO_USER.get(k, k)] = v
    return out


# ---------- documents ----------


def insert_document(file_name: str, sha256: str | None = None) -> int:
    conn = get_connection()
    try:
        cur = conn.execute(
            "INSERT INTO documents(file_name, sha256) VALUES(?, ?)",
            (file_name, sha256),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def get_document_by_name(file_name: str) -> dict[str, Any] | None:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM documents WHERE file_name = ?", (file_name,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def upsert_document(file_name: str, sha256: str | None = None) -> int:
    """按文件名取或建文档, 返回 doc_id。"""
    doc = get_document_by_name(file_name)
    if doc:
        return doc["id"]
    return insert_document(file_name, sha256)


# ---------- ocr_rows ----------


def insert_rows(doc_id: int, rows: list[dict[str, Any]]) -> int:
    """插入一批识别行(状态 pending), 返回行数。rows 的键用无下划线名(desc/date/from/sum)。"""
    conn = get_connection()
    try:
        for seq, r in enumerate(rows, start=1):
            values = [
                r.get("desc", r.get("desc_", "")),
                r.get("date", r.get("date_", "")),
                r.get("from", r.get("from_", "")),
                r.get("item", ""),
                r.get("amount", ""),
                r.get("price", ""),
                r.get("tax", ""),
                r.get("sum", r.get("sum_", "")),
            ]
            conn.execute(
                f"INSERT INTO ocr_rows(doc_id, seq, {','.join(ROW_FIELDS)}, status) "
                f"VALUES (?, ?, {','.join(['?'] * len(ROW_FIELDS))}, 'pending')",
                (doc_id, seq, *values),
            )
        conn.commit()
        return len(rows)
    finally:
        conn.close()


def rows_by_doc(doc_id: int, include: str = "all") -> list[dict[str, Any]]:
    """查询某图的行(用户友好键)。include: all/pending/confirmed。"""
    conn = get_connection()
    try:
        sql = "SELECT * FROM ocr_rows WHERE doc_id = ?"
        if include != "all":
            sql += " AND status = ?"
        cur = conn.execute(sql, (doc_id,) if include == "all" else (doc_id, include))
        return [_user_row(dict(r)) for r in cur.fetchall()]
    finally:
        conn.close()


def delete_rows_by_doc(doc_id: int) -> int:
    """删除某图的全部行(同图重识别前调用)。返回删除行数。"""
    conn = get_connection()
    try:
        cur = conn.execute("DELETE FROM ocr_rows WHERE doc_id = ?", (doc_id,))
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def confirm_doc(doc_id: int, reviewer: str = "agent") -> int:
    """把某图全部行标记为已确认。返回更新行数。"""
    conn = get_connection()
    try:
        now = "datetime('now','localtime')"
        cur = conn.execute(
            f"UPDATE ocr_rows SET status='confirmed', modified_by=?, modified_at={now} "
            "WHERE doc_id=?",
            (reviewer, doc_id),
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def update_row(
    row_id: int,
    fields: dict[str, Any],
    reviewer: str = "manual",
) -> bool:
    """人工修改单行。fields 键为无下划线用户键(desc/date/from/sum/item/...)。"""
    mapped: dict[str, Any] = {}
    for k, v in fields.items():
        dbk = _USER_TO_DB.get(k, k)
        if dbk in ROW_FIELDS:
            mapped[dbk] = v
    if not mapped:
        return False
    sets = ", ".join(f"{k} = ?" for k in mapped)
    conn = get_connection()
    try:
        conn.execute(
            f"UPDATE ocr_rows SET {sets}, modified_by=?, "
            "modified_at=datetime('now','localtime') WHERE id=?",
            (*mapped.values(), reviewer, row_id),
        )
        conn.commit()
        return True
    finally:
        conn.close()


# ---------- 聚合/检索 ----------


def confirmed_rows(filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """已确认的全部行(用户键, 可带 desc/item/date 过滤), 供 RAG/统计使用。"""
    conn = get_connection()
    try:
        sql = (
            "SELECT r.*, d.file_name FROM ocr_rows r "
            "JOIN documents d ON d.id = r.doc_id WHERE r.status='confirmed'"
        )
        params: list[Any] = []
        if filters:
            for k, v in filters.items():
                dbk = _USER_TO_DB.get(k, k)
                if v is not None and v != "" and dbk in ROW_FIELDS:
                    sql += f" AND r.{dbk} LIKE ?"
                    params.append(f"%{v}%")
        cur = conn.execute(sql + " ORDER BY r.date_, r.desc_", params)
        return [_user_row(dict(r)) for r in cur.fetchall()]
    finally:
        conn.close()


def stats() -> dict[str, Any]:
    conn = get_connection()
    try:
        docs = conn.execute("SELECT COUNT(*) c FROM documents").fetchone()["c"]
        rows = conn.execute("SELECT COUNT(*) c FROM ocr_rows").fetchone()["c"]
        confirmed = conn.execute(
            "SELECT COUNT(*) c FROM ocr_rows WHERE status='confirmed'"
        ).fetchone()["c"]
        return {"documents": docs, "rows": rows, "confirmed": confirmed}
    finally:
        conn.close()


def all_docs_with_stats() -> list[dict[str, Any]]:
    """列出所有单据(用户键)及行数/已确认行数。"""
    conn = get_connection()
    try:
        sql = (
            "SELECT d.id, d.file_name, d.uploaded_at, "
            "COUNT(r.id) AS row_count, "
            "COALESCE(SUM(CASE WHEN r.status='confirmed' THEN 1 ELSE 0 END),0) AS confirmed_count "
            "FROM documents d LEFT JOIN ocr_rows r ON r.doc_id = d.id "
            "GROUP BY d.id ORDER BY d.id"
        )
        return [dict(r) for r in conn.execute(sql).fetchall()]
    finally:
        conn.close()
