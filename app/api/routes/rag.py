"""RAG 出口路由：增量数据 + 墓碑通知。"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.schemas import RagDataOut, RagRow, RagTombstonesOut
from app.services import rag_service

router = APIRouter(tags=["rag"])


def _parse_since(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        raise HTTPException(status_code=422, detail="since 需为 ISO8601 时间") from None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


@router.get("/rag/data", response_model=RagDataOut)
async def rag_data(
    updated_since: str | None = Query(default=None, description="ISO8601，如 2025-01-01T00:00:00"),
    cursor: str | None = Query(default=None),
    doc_type: str | None = Query(default=None),
    page_size: int = Query(default=100, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
):
    """仅 approved 文档的最新内容版本，updated_at+id 游标分页（状态型增量，按 document_id upsert）。"""
    since = _parse_since(updated_since)
    try:
        page = await rag_service.fetch_rag_data(
            session, since=since, cursor=cursor, doc_type=doc_type, page_size=page_size
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return RagDataOut(
        items=[RagRow(**row) for row in page.items],
        next_cursor=page.next_cursor,
    )


@router.get("/rag/tombstones", response_model=RagTombstonesOut)
async def rag_tombstones(
    updated_since: str | None = Query(default=None, description="ISO8601"),
    cursor: str | None = Query(default=None),
    page_size: int = Query(default=100, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
):
    """通知曾 approved 后状态变化的文档（删除/降权旧索引）。"""
    since = _parse_since(updated_since) or rag_service.EPOCH
    try:
        page = await rag_service.fetch_tombstones(
            session, since=since, cursor=cursor, page_size=page_size
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return RagTombstonesOut(items=page.items, next_cursor=page.next_cursor)
