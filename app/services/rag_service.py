"""RAG 出口：已审核数据的增量拉取与墓碑通知。

语义（写入 rag-handoff.md 文档）：
- rag/data 为状态型增量：RAG 侧按 document_id upsert，游标推进幂等；
- rag/tombstones 通知"曾 approved 后状态变化"的文档 id，RAG 侧应删除/降权旧索引。
"""

from __future__ import annotations

import base64
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import models as m
from app.models import DocStatus

PAGE_SIZE_DEFAULT = 100
PAGE_SIZE_MAX = 500
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def _ensure_aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


def _encode_cursor(updated_at: datetime, doc_id) -> str:
    payload = json.dumps([updated_at.isoformat(), str(doc_id)])
    return base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii")


def _decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID] | None:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii"))
        ts, uid = json.loads(raw)
        return _ensure_aware(datetime.fromisoformat(ts)), uuid.UUID(str(uid))
    except Exception:
        return None


@dataclass
class RagPage:
    items: list[dict]
    next_cursor: str | None = None


async def _latest_version_for(session: AsyncSession, document_id) -> m.ContentVersion | None:
    return await m.get_latest_version(session, document_id)


async def fetch_rag_data(
    session: AsyncSession,
    *,
    since: datetime | None = None,
    cursor: str | None = None,
    doc_type: str | None = None,
    page_size: int = PAGE_SIZE_DEFAULT,
) -> RagPage:
    """拉取 approved 文档的最新内容版本（游标 = 末行 (updated_at,id)，幂等）。"""
    page_size = max(1, min(page_size, PAGE_SIZE_MAX))

    cursor_ts: datetime | None = None
    cursor_id = None
    if cursor is not None:
        decoded = _decode_cursor(cursor)
        if decoded is None:
            raise ValueError("cursor 非法")
        cursor_ts, cursor_id = decoded
        since = cursor_ts
    elif since is None:
        since = EPOCH
    else:
        since = _ensure_aware(since)

    conditions = [m.Document.status == DocStatus.APPROVED]
    if doc_type:
        conditions.append(m.Document.doc_type == doc_type)
    if cursor_ts is not None and cursor_id is not None:
        # 严格大于游标位置（同秒多文档也正确翻页）
        conditions.append(
            (m.Document.updated_at > cursor_ts)
            | ((m.Document.updated_at == cursor_ts) & (m.Document.id > cursor_id))
        )
    else:
        conditions.append(m.Document.updated_at >= since)

    stmt: Select = (
        select(m.Document)
        .where(*conditions)
        .order_by(m.Document.updated_at, m.Document.id)
        .limit(page_size + 1)
    )
    result = await session.execute(stmt)
    fetched = list(result.scalars().all())

    has_more = len(fetched) > page_size
    docs = fetched[:page_size]
    next_cursor = None
    if has_more:
        last = docs[-1]
        next_cursor = _encode_cursor(last.updated_at, last.id)

    items: list[dict] = []
    for doc in docs:
        version = await _latest_version_for(session, doc.id)
        items.append(
            {
                "document_id": doc.id,
                "doc_type": doc.doc_type,
                "version_no": version.version_no if version else doc.current_version,
                "text": version.text if version else None,
                "fields": version.fields if version else {},
                "updated_at": doc.updated_at,
                "reviewed_at": doc.latest_approved_at,
            }
        )
    return RagPage(items=items, next_cursor=next_cursor)


async def fetch_tombstones(
    session: AsyncSession,
    *,
    since: datetime,
    cursor: str | None = None,
    page_size: int = PAGE_SIZE_DEFAULT,
) -> RagPage:
    """通知"曾 approved 后状态再变化"的文档（供 RAG 清理旧索引）。

    游标语义与 rag/data 一致：(updated_at,id) 严格递增，重复拉取幂等。
    """
    page_size = max(1, min(page_size, PAGE_SIZE_MAX))
    since = _ensure_aware(since)
    cursor_ts: datetime | None = None
    cursor_id = None
    if cursor is not None:
        decoded = _decode_cursor(cursor)
        if decoded is None:
            raise ValueError("cursor 非法")
        cursor_ts, cursor_id = decoded
        since = cursor_ts

    conditions = [
        m.Document.status != DocStatus.APPROVED,
        m.Document.latest_approved_at.is_not(None),
    ]
    if cursor_ts is not None and cursor_id is not None:
        conditions.append(
            (m.Document.updated_at > cursor_ts)
            | ((m.Document.updated_at == cursor_ts) & (m.Document.id > cursor_id))
        )
    else:
        conditions.append(m.Document.updated_at >= since)

    stmt = (
        select(m.Document)
        .where(*conditions)
        .order_by(m.Document.updated_at, m.Document.id)
        .limit(page_size + 1)
    )
    result = await session.execute(stmt)
    fetched = list(result.scalars().all())

    has_more = len(fetched) > page_size
    docs = fetched[:page_size]
    next_cursor = None
    if has_more:
        last = docs[-1]
        next_cursor = _encode_cursor(last.updated_at, last.id)

    items = []
    for doc in docs:
        items.append(
            {
                "document_id": doc.id,
                "status": doc.status,
                "latest_approved_at": doc.latest_approved_at,
                "updated_at": doc.updated_at,
            }
        )
    return RagPage(items=items, next_cursor=next_cursor)
