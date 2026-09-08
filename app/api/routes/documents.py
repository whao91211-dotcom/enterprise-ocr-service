"""文档路由：详情 / OCR 重试 / 历史 / 审计 / 原图。"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import models as m
from app.api.deps import CurrentUser, get_current_user
from app.api.serializers import document_to_out, verify_image_sig
from app.core.db import get_session
from app.schemas import (
    AuditActionOut,
    ContentVersionOut,
    DocumentOut,
    HistoryOut,
)
from app.services import pipeline as pipeline_svc
from app.services import storage

router = APIRouter(tags=["documents"])


async def _get_doc_or_404(session: AsyncSession, document_id) -> m.Document:
    doc = await session.get(m.Document, document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="文档不存在")
    return doc


@router.get("/documents/{document_id}", response_model=DocumentOut)
async def document_detail(
    document_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
):
    """文档详情：元数据 + 最新 OCR 结果 + 最新内容版本 + 模板字段定义 + 原图地址。"""
    doc = await _get_doc_or_404(session, document_id)
    return await document_to_out(session, doc)


@router.get("/documents/{document_id}/history", response_model=HistoryOut)
async def document_history(
    document_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
):
    """版本链 + 审核审计（供审核 UI 对比与追溯）。"""
    await _get_doc_or_404(session, document_id)
    versions = (
        (
            await session.execute(
                select(m.ContentVersion)
                .where(m.ContentVersion.document_id == document_id)
                .order_by(m.ContentVersion.version_no)
            )
        )
        .scalars()
        .all()
    )
    audit = (
        (
            await session.execute(
                select(m.ReviewAction)
                .where(m.ReviewAction.document_id == document_id)
                .order_by(m.ReviewAction.id)
            )
        )
        .scalars()
        .all()
    )
    return HistoryOut(
        versions=[ContentVersionOut.model_validate(v) for v in versions],
        audit=[AuditActionOut.model_validate(a) for a in audit],
    )


@router.get("/documents/{document_id}/audit", response_model=list[AuditActionOut])
async def document_audit(
    document_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
):
    await _get_doc_or_404(session, document_id)
    audit = (
        (
            await session.execute(
                select(m.ReviewAction)
                .where(m.ReviewAction.document_id == document_id)
                .order_by(m.ReviewAction.id)
            )
        )
        .scalars()
        .all()
    )
    return [AuditActionOut.model_validate(a) for a in audit]


@router.get("/documents/{document_id}/image")
async def document_image(
    document_id: uuid.UUID,
    expires: int | None = Query(default=None),
    sig: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
):
    """原图访问：签名短时效 URL（浏览器 <img> 无法带 header，故用 query 签名）。"""
    doc = await _get_doc_or_404(session, document_id)
    if not verify_image_sig(document_id, expires, sig):
        raise HTTPException(status_code=403, detail="图片链接无效或已过期")
    try:
        path = storage.resolve_path(doc.storage_key)
    except ValueError:
        raise HTTPException(status_code=404, detail="图片文件不存在") from None
    if not path.is_file():
        raise HTTPException(status_code=404, detail="图片文件不存在")
    return FileResponse(path, media_type=doc.content_type or "application/octet-stream")


@router.post("/documents/{document_id}/ocr/retry", response_model=DocumentOut)
async def ocr_retry(
    document_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(get_current_user),
):
    """OCR 失败后重试：新建一次 ocr_run 记录，成功后生成 v1 内容版本。"""
    doc = await session.get(m.Document, document_id, with_for_update=True)
    if doc is None:
        raise HTTPException(status_code=404, detail="文档不存在")
    if doc.status != m.DocStatus.OCR_FAILED:
        raise HTTPException(status_code=409, detail=f"仅 ocr_failed 状态可重试，当前: {doc.status}")

    template = await m.get_template(session, doc.doc_type)
    if template is None:
        raise HTTPException(status_code=422, detail="单据类型模板不存在")

    try:
        image_bytes = storage.load_image(doc.storage_key)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="原图文件丢失，无法重试") from None
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    await pipeline_svc._attempt_ocr(session, doc, template, image_bytes, doc.content_type)
    # 记录审计
    session.add(
        m.ReviewAction(
            document_id=doc.id,
            action=m.ReviewActionKind.RETRY,
            from_status=m.DocStatus.OCR_FAILED,
            to_status=doc.status,
            reviewer=user.id,
            comment="OCR retry",
        )
    )
    await session.commit()
    return await document_to_out(session, doc)
