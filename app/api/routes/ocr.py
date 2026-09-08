"""单张图片识别路由：multipart 在线上传 + JSON 单张提交。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user
from app.api.routes.ingest import _run_sync
from app.api.serializers import document_to_out
from app.core.db import get_session
from app.schemas import DocumentOut, SingleSubmitIn
from app.services import ingest as ingest_svc
from app.services.pipeline import PipelineError, run_single_pipeline

router = APIRouter(tags=["ocr"])


@router.post("/ocr/upload", response_model=DocumentOut, status_code=201)
async def upload_image(
    file: UploadFile = File(...),
    doc_type: str = Form(...),
    session: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(get_current_user),
):
    """在线上传单张图片并同步执行 OCR（multipart: file + doc_type）。"""
    data = await file.read()
    try:
        real_ext = ingest_svc.check_upload_bytes(data, file.filename or "")
    except ingest_svc.IngestError as exc:
        raise HTTPException(status_code=400, detail=f"图片校验失败: {exc}") from exc

    try:
        outcome = await run_single_pipeline(
            session,
            image_bytes=data,
            real_ext=real_ext,
            file_name=file.filename,
            content_type=file.content_type,
            ingest_source="multipart",
            original_ref=None,
            doc_type=doc_type,
            created_by=user.id,
        )
    except PipelineError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await session.commit()
    return await document_to_out(session, outcome.document)


@router.post("/ocr/submit", response_model=DocumentOut, status_code=201)
async def submit_image(
    body: SingleSubmitIn,
    session: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(get_current_user),
):
    """对本地路径/URL/现有API 可访问的图片直接同步识别单张。"""
    return await _run_sync(
        session,
        source=body.source.type,
        ref=body.source.ref,
        doc_type=body.doc_type,
        created_by=user.id,
    )
