"""ORM → API 响应模型组装（含原图签名 URL）。"""

from __future__ import annotations

import hashlib
import hmac
import time
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import models as m
from app.core.config import get_settings
from app.schemas import DocumentOut


def sign_image_url(document_id: uuid.UUID) -> str:
    """生成文档原图访问 URL（带 HMAC 签名与时效；无签名密钥时本地直放）。"""
    settings = get_settings()
    path = f"{settings.api_prefix}/documents/{document_id}/image"
    if not settings.signing_key:
        return path
    expires = int(time.time()) + settings.image_url_ttl_seconds
    sig = hmac.new(
        settings.signing_key.encode("utf-8"), f"{document_id}:{expires}".encode(), hashlib.sha256
    ).hexdigest()
    return f"{path}?expires={expires}&sig={sig}"


def verify_image_sig(document_id: uuid.UUID, expires: int | None, sig: str | None) -> bool:
    settings = get_settings()
    if not settings.signing_key:
        return True
    if not expires or not sig:
        return False
    if int(time.time()) > expires:
        return False
    expect = hmac.new(
        settings.signing_key.encode("utf-8"), f"{document_id}:{expires}".encode(), hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expect, sig)


async def latest_ocr_run(session: AsyncSession, document_id: uuid.UUID) -> m.OcrRun | None:
    return (
        await session.execute(
            select(m.OcrRun)
            .where(m.OcrRun.document_id == document_id)
            .order_by(m.OcrRun.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def document_to_out(session: AsyncSession, doc: m.Document) -> DocumentOut:
    # 注意：异步 session 内禁止隐式懒加载关系属性（MissingGreenlet），一律显式查询
    template = await m.get_template(session, doc.doc_type)
    latest_ocr = await latest_ocr_run(session, doc.id)
    latest_content = await m.get_latest_version(session, doc.id)
    out = DocumentOut(
        id=doc.id,
        doc_type=doc.doc_type,
        template_name=template.name if template else None,
        template_field_defs=(template.field_defs or []) if template else [],
        ingest_source=doc.ingest_source,
        original_ref=doc.original_ref,
        file_name=doc.file_name,
        content_type=doc.content_type,
        sha256=doc.sha256,
        status=doc.status,
        current_version=doc.current_version,
        error_message=doc.error_message,
        created_by=doc.created_by,
        reviewed_at=doc.reviewed_at,
        latest_approved_at=doc.latest_approved_at,
        created_at=doc.created_at,
        updated_at=doc.updated_at,
        image_url=sign_image_url(doc.id),
    )
    if latest_ocr is not None:
        out.latest_ocr = latest_ocr  # type: ignore[assignment]
    if latest_content is not None:
        out.latest_content = latest_content  # type: ignore[assignment]
    return out
