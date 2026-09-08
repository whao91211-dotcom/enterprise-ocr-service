"""识别路由（简化版）：multipart 上传图片 → 同步 OCR → 8列清洗 → 落盘 csv。"""

from __future__ import annotations

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from app.schemas import RecognizeOut
from app.services.cleaner import SALES_KEYS
from app.services.recognize import RecognizeError, check_image_bytes, recognize_sales

router = APIRouter(tags=["ocr"])


@router.post("/ocr/recognize", response_model=RecognizeOut, status_code=200)
async def recognize_image(
    file: UploadFile = File(...),
    doc_type: str = Form("sales"),
    include_raw: bool = Form(False),
):
    """上传销售单据图片，识别为 8 列 CSV 并落盘到 output/。

    - file: 图片(png/jpg/jpeg/webp/bmp)
    - doc_type: 单据类型，当前仅支持 sales
    - include_raw: 是否在响应中附带模型原始返回（默认否）
    """
    data = await file.read()
    try:
        check_image_bytes(data, file.filename or "")
    except RecognizeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        outcome = await recognize_sales(data, file_name=file.filename, doc_type=doc_type)
    except RecognizeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return RecognizeOut(
        ok=True,
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
