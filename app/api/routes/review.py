"""审核工作流路由：待审队列 / approve-reject / reopen。"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import models as m
from app.api.deps import CurrentUser, get_current_user
from app.api.serializers import document_to_out
from app.core.db import get_session
from app.schemas import (
    DocumentOut,
    DocumentSummary,
    ReopenIn,
    ReviewIn,
    ReviewOut,
    ReviewQueueOut,
)
from app.services import review_service

router = APIRouter(tags=["review"])


@router.get("/review/queue", response_model=ReviewQueueOut)
async def review_queue(
    status: str = Query(default=m.DocStatus.PENDING_REVIEW),
    doc_type: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
):
    """审核队列（默认 pending_review）。附 OCR 初值摘要供列表预览。"""
    where = [m.Document.status == status]
    if doc_type:
        where.append(m.Document.doc_type == doc_type)
    total = (
        await session.execute(select(func.count()).select_from(m.Document).where(*where))
    ).scalar_one()
    docs = (
        (
            await session.execute(
                select(m.Document)
                .where(*where)
                .order_by(m.Document.created_at)
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .scalars()
        .all()
    )
    items: list[DocumentSummary] = []
    for doc in docs:
        latest = await m.get_latest_version(session, doc.id)
        template = await m.get_template(session, doc.doc_type)
        items.append(
            DocumentSummary(
                id=doc.id,
                doc_type=doc.doc_type,
                template_name=template.name if template else None,
                file_name=doc.file_name,
                status=doc.status,
                current_version=doc.current_version,
                created_at=doc.created_at,
                updated_at=doc.updated_at,
                preview_text=(latest.text or "")[:300] if latest else None,
                preview_fields=latest.fields if latest else None,
            )
        )
    return ReviewQueueOut(total=total, page=page, page_size=page_size, items=items)


@router.post("/documents/{document_id}/review", response_model=ReviewOut)
async def review_document(
    document_id: uuid.UUID,
    body: ReviewIn,
    session: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(get_current_user),
):
    """审核动作：approve(人工修正后可提交终值) / reject(必须给打回原因)。"""
    try:
        if body.action == "approve":
            if body.expected_version is None:
                raise review_service.ReviewError("approve 必须携带 expected_version", 422)
            corrections = (
                body.corrections.model_dump(exclude_none=True) if body.corrections else None
            )
            result = await review_service.approve_document(
                session,
                document_id,
                reviewer=user.id,
                expected_version=body.expected_version,
                corrections=corrections,
                comment=body.comment,
            )
        else:
            result = await review_service.reject_document(
                session,
                document_id,
                reviewer=user.id,
                comment=body.comment,
            )
    except review_service.ReviewError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    except review_service.ReviewValidationFailed as exc:
        raise HTTPException(status_code=422, detail=exc.result.validation_errors) from exc

    await session.commit()
    return ReviewOut(
        document_id=document_id,
        status=result.status,
        current_version=result.current_version,
        new_version=result.new_version,
        field_changes=result.field_changes,
    )


@router.post("/documents/{document_id}/reopen", response_model=DocumentOut)
async def reopen_document(
    document_id: uuid.UUID,
    body: ReopenIn | None = None,
    session: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(get_current_user),
):
    """approved/rejected → pending_review 返修（RAG 侧将由 tombstone 感知）。"""
    body = body or ReopenIn()
    try:
        await review_service.reopen_document(
            session,
            document_id,
            reviewer=user.id,
            comment=body.comment,
        )
    except review_service.ReviewError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    await session.commit()
    doc = await session.get(m.Document, document_id)
    assert doc is not None
    return await document_to_out(session, doc)
