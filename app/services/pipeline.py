"""单文档处理管线：接入字节 → 去重 → 存图 → 建文档 → 调 OCR → 落初值版本。

被三类入口复用：multipart 单张上传、JSON 单张提交、批量队列 worker。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import models as m
from app.core.config import get_settings
from app.services import ingest as ingest_svc
from app.services import ocr_client, prompt_builder, result_parser, storage, validation


class PipelineError(Exception):
    """文档处理失败（消息面向调用方）。"""


@dataclass
class PipelineOutcome:
    document: m.Document
    duplicate: bool = False  # sha256 命中既有文档
    ocr_attempted: bool = False
    message: str = ""


async def find_existing_by_sha(session: AsyncSession, sha256: str) -> m.Document | None:
    res = await session.execute(select(m.Document).where(m.Document.sha256 == sha256))
    return res.scalar_one_or_none()


async def run_single_pipeline(
    session: AsyncSession,
    *,
    image_bytes: bytes,
    real_ext: str,
    file_name: str | None,
    content_type: str | None,
    ingest_source: str,
    original_ref: str | None,
    doc_type: str,
    created_by: str,
) -> PipelineOutcome:
    """接入图片并跑一次 OCR；成功与否都落库为确定状态。"""
    template = await m.get_template(session, doc_type)
    if template is None:
        raise PipelineError(f"单据类型模板不存在: {doc_type}")

    sha = ingest_svc.sha256_of(image_bytes)
    existing = await find_existing_by_sha(session, sha)
    if existing is not None:
        return PipelineOutcome(document=existing, duplicate=True, message="图片已存在(sha256 相同)，返回既有文档")

    storage_key = storage.save_image(image_bytes, real_ext)
    doc = m.Document(
        doc_type=doc_type,
        ingest_source=ingest_source,
        original_ref=original_ref or None,
        file_name=file_name,
        content_type=content_type or f"image/{real_ext}",
        storage_key=storage_key,
        sha256=sha,
        status=m.DocStatus.OCR_PENDING,
        created_by=created_by,
    )
    session.add(doc)
    await session.flush()
    await _attempt_ocr(session, doc, template, image_bytes, content_type)
    return PipelineOutcome(document=doc, ocr_attempted=True)


async def _attempt_ocr(
    session: AsyncSession,
    doc: m.Document,
    template: m.DocTemplate,
    image_bytes: bytes,
    content_type: str | None,
) -> None:
    """执行一次 OCR 并落库。异常分类：配置/解析失败 → 文档 ocr_failed 状态，不抛出。"""
    settings = get_settings()
    field_defs = template.field_defs or []
    if template.row_mode:
        instruction = prompt_builder.build_csv_instruction(template.code, field_defs)
    else:
        instruction = prompt_builder.build_instruction_json(template.code, template.name, field_defs)
    run = m.OcrRun(document_id=doc.id, model_name=settings.ocr_model or "(unset)")
    session.add(run)
    try:
        outcome = await ocr_client.call_ocr(settings, image_bytes, content_type, instruction)
    except ocr_client.OcrConfigError as exc:
        run.status = m.OcrRunStatus.FAILED
        run.error = str(exc)
        doc.status = m.DocStatus.OCR_FAILED
        doc.error_message = str(exc)
        return
    except ocr_client.OcrCallError as exc:
        run.status = m.OcrRunStatus.FAILED
        run.error = str(exc)
        doc.status = m.DocStatus.OCR_FAILED
        doc.error_message = f"OCR 调用失败: {exc}"
        return
    except Exception as exc:  # 兜底：任何异常都转为失败态，原始请求信息不丢失
        run.status = m.OcrRunStatus.FAILED
        run.error = f"未预期异常: {exc!r}"
        doc.status = m.DocStatus.OCR_FAILED
        doc.error_message = f"OCR 处理异常: {exc}"
        return

    run.status = m.OcrRunStatus.SUCCESS
    run.raw_response = outcome.raw_response
    run.latency_ms = outcome.latency_ms

    try:
        parsed = result_parser.parse_content(outcome.content)
    except result_parser.ContentParseError as exc:
        run.status = m.OcrRunStatus.FAILED
        run.error = str(exc)
        run.raw_response = outcome.raw_response
        doc.status = m.DocStatus.OCR_FAILED
        doc.error_message = f"OCR 内容解析失败: {exc}"
        return

    run.parsed_text = parsed.text or None
    run.parsed_fields = parsed.fields or None
    run.parse_warnings = parsed.warnings or None

    if template.row_mode:
        # 多行记录契约：CSV 行 → 按 field_defs 顺序映射为 dict 列表
        keys = [d["key"] for d in field_defs]
        raw_rows: list[dict] = []
        for cells in parsed.rows:
            if not cells:
                continue
            row = {}
            for i, k in enumerate(keys):
                row[k] = cells[i].strip() if i < len(cells) else None
            raw_rows.append(row)
        vout = validation.validate_rows(raw_rows, field_defs, strict=False)
        for e in vout.errors:
            parsed.warnings.append(f"{e.label}: {e.message}")
        stored_fields: dict = {"rows": vout.cleaned if vout.cleaned else raw_rows}
    else:
        # 单文档字段契约
        vout = validation.validate_fields(parsed.fields, field_defs, strict=False)
        if vout.errors:
            for e in vout.errors:
                parsed.warnings.append(f"{e.label}: {e.message}")
        stored_fields = vout.cleaned if vout.cleaned else (parsed.fields or {})

    v1 = m.ContentVersion(
        document_id=doc.id,
        version_no=1,
        kind=m.VersionKind.OCR,
        text=parsed.text or None,
        fields=stored_fields,
        editor="ocr",
    )
    session.add(v1)
    doc.status = m.DocStatus.PENDING_REVIEW
    doc.current_version = 1
    doc.error_message = None
    await session.flush()


def safe_uuid(value: str | uuid.UUID) -> uuid.UUID:
    return uuid.UUID(str(value))
