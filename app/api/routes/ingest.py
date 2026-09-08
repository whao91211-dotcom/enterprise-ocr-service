"""批量补识别队列与图片接入路由。"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import models as m
from app.api.deps import CurrentUser, get_current_user
from app.api.serializers import document_to_out
from app.core.db import get_session
from app.schemas import (
    DocumentOut,
    ImportIn,
    ImportResult,
    MessageOut,
    QueueItemOut,
    QueueProgress,
)
from app.services import batch_worker
from app.services import ingest as ingest_svc
from app.services.pipeline import PipelineError, run_single_pipeline

router = APIRouter(tags=["ingest"])


@router.post("/ocr/import", response_model=ImportResult, status_code=202)
async def import_batch(
    body: ImportIn,
    session: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(get_current_user),
):
    """批量补识别：提交本地路径/URL/API 清单，入队后由 worker 逐个处理。"""
    template = await m.get_template(session, body.doc_type)
    if template is None:
        raise HTTPException(status_code=422, detail=f"单据类型模板不存在: {body.doc_type}")

    batch_id = uuid.uuid4()
    items: list[m.IngestQueueItem] = []
    for item in body.items:
        items.append(
            m.IngestQueueItem(
                batch_id=batch_id,
                source=item.source,
                original_ref=item.ref,
                doc_type=body.doc_type,
                status=m.QueueStatus.QUEUED,
            )
        )
    session.add_all(items)
    await session.commit()
    # 刷新获得 id
    for it in items:
        await session.refresh(it)
    return ImportResult(
        batch_id=batch_id,
        accepted=len(items),
        items=[QueueItemOut.model_validate(it) for it in items],
    )


@router.get("/ocr/import/{batch_id}", response_model=QueueProgress)
async def import_progress(
    batch_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
):
    """查询批次进度（逐项状态）。"""
    counts = dict(
        (
            await session.execute(
                select(m.IngestQueueItem.status, func.count())
                .where(m.IngestQueueItem.batch_id == batch_id)
                .group_by(m.IngestQueueItem.status)
            )
        ).all()
    )
    total = sum(counts.values())
    res = await session.execute(
        select(m.IngestQueueItem)
        .where(m.IngestQueueItem.batch_id == batch_id)
        .order_by(m.IngestQueueItem.id)
    )
    items = list(res.scalars().all())
    return QueueProgress(
        batch_id=batch_id,
        total=total,
        queued=counts.get(m.QueueStatus.QUEUED, 0),
        processing=counts.get(m.QueueStatus.PROCESSING, 0),
        done=counts.get(m.QueueStatus.DONE, 0),
        failed=counts.get(m.QueueStatus.FAILED, 0),
        items=[QueueItemOut.model_validate(it) for it in items],
    )


@router.post("/queue/{item_id}/retry", response_model=MessageOut)
async def retry_queue_item(
    item_id: int,
    session: AsyncSession = Depends(get_session),
):
    """失败队列项重新入队。"""
    ok = await batch_worker.retry_queue_item(session, item_id)
    if not ok:
        raise HTTPException(status_code=404, detail="队列项不存在")
    return MessageOut(message="已重新入队")


# ---------- 单张同步：JSON 来源（本地路径/URL/API） ----------


async def _run_sync(
    session: AsyncSession,
    *,
    source: str,
    ref: str,
    doc_type: str,
    created_by: str,
) -> DocumentOut:
    try:
        data, real_ext, display = await ingest_svc.fetch_bytes(source, ref)
        outcome = await run_single_pipeline(
            session,
            image_bytes=data,
            real_ext=real_ext,
            file_name=display,
            content_type=f"image/{real_ext}",
            ingest_source=source,
            original_ref=ref,
            doc_type=doc_type,
            created_by=created_by,
        )
    except ingest_svc.IngestError as exc:
        raise HTTPException(status_code=400, detail=f"图片接入失败: {exc}") from exc
    except PipelineError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await session.commit()
    return await document_to_out(session, outcome.document)
