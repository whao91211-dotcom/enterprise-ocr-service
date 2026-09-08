"""识别与人工确认路由（简化版）：multipart 上传 → OCR → 8列清洗 → 总 CSV。

动作:
  POST /ocr/recognize    上传识别（force=true 强制覆盖已确认图）
  POST /ocr/confirm      把某图标记为已确认（人工改完 Excel 后调用）
  GET  /ocr/sources      列出各图状态（待确认/已确认）
"""

from __future__ import annotations

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from app.schemas import (
    ConfirmIn,
    ConfirmOut,
    RecognizeOut,
    SourcesOut,
    SourceStatus,
)
from app.services import csv_store
from app.services.cleaner import SALES_KEYS
from app.services.recognize import RecognizeError, check_image_bytes, recognize_sales

router = APIRouter(tags=["ocr"])


@router.post("/ocr/recognize", response_model=RecognizeOut, status_code=200)
async def recognize_image(
    file: UploadFile = File(...),
    doc_type: str = Form("sales"),
    include_raw: bool = Form(False),
    force: bool = Form(False),
):
    """上传销售单据图片，识别为 8 列 CSV 并写入 output/all_sales.csv。

    - doc_type: 单据类型，当前仅支持 sales
    - include_raw: 是否在响应中附带模型原始返回（默认否）
    - force: true 时即使该图已人工确认也强制重新识别覆盖
    """
    data = await file.read()
    try:
        check_image_bytes(data, file.filename or "")
    except RecognizeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        outcome = await recognize_sales(
            data, file_name=file.filename, doc_type=doc_type, force=force
        )
    except RecognizeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return RecognizeOut(
        ok=True,
        skipped=outcome.skipped,
        message=outcome.message,
        doc_type=outcome.doc_type,
        file_name=outcome.file_name,
        csv_file=outcome.csv_path.name,
        csv_path=str(outcome.csv_path),
        row_count=len(outcome.rows),
        rows=outcome.rows,
        columns=list(SALES_KEYS),
        warnings=outcome.warnings,
        model=outcome.model_name,
        latency_ms=outcome.latency_ms,
        raw=outcome.raw_content if include_raw else None,
    )


@router.post("/ocr/confirm", response_model=ConfirmOut, status_code=200)
async def confirm_image(body: ConfirmIn):
    """把某图全部行标记为「已确认」，记录修改人/时间。"""
    updated = csv_store.confirm_source(body.source_file, body.reviewer)
    if updated == 0:
        raise HTTPException(
            status_code=404,
            detail=f"all_sales.csv 中找不到源图片: {body.source_file}",
        )
    return ConfirmOut(
        ok=True, source_file=body.source_file, confirmed_rows=updated
    )


@router.get("/ocr/sources", response_model=SourcesOut, status_code=200)
async def list_sources():
    """列出每张图的状态（按源文件名排序），供人工核对进度。"""
    items = [SourceStatus(**s) for s in csv_store.list_sources()]
    pending = sum(1 for s in items if s.status == csv_store.STATUS_PENDING)
    confirmed = sum(1 for s in items if s.status == csv_store.STATUS_CONFIRMED)
    return SourcesOut(
        total_sources=len(items), pending=pending, confirmed=confirmed, items=items
    )
