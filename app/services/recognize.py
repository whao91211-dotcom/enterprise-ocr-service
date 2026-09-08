"""识别编排（简化版）：图片字节 → OCR → 解析 CSV → 8 列清洗 → 落盘 csv。

不依赖数据库；只对 sales（销售单据）8 列契约服务。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from app.core.config import get_settings
from app.services import cleaner, csv_store, ocr_client, prompt_builder, result_parser


class RecognizeError(Exception):
    """识别失败（消息面向调用方）。"""


@dataclass
class RecognizeOutcome:
    csv_path: Path
    file_name: str | None
    doc_type: str
    rows: list[dict] = field(default_factory=list)  # 清洗后行（8 列 key）
    raw_content: str = ""
    warnings: list[str] = field(default_factory=list)
    model_name: str = ""
    latency_ms: int = 0


SUPPORTED_DOC_TYPES = {"sales"}


def check_image_bytes(data: bytes, file_name: str | None) -> str:
    """校验图片字节与扩展名，返回真实扩展名(小写去点)。抛 RecognizeError。"""
    settings = get_settings()
    if not data:
        raise RecognizeError("上传文件为空")
    if len(data) > settings.max_image_bytes:
        raise RecognizeError(f"文件超过大小限制 {settings.max_image_bytes // (1024*1024)}MB")
    if file_name and "." in file_name:
        ext = file_name.rsplit(".", 1)[-1].lower()
    else:
        ext = "png"  # 无扩展名默认按 png 试
    if ext not in settings.allowed_exts_set:
        raise RecognizeError(f"不支持的图片类型: {ext}（允许: {sorted(settings.allowed_exts_set)}）")
    return ext


async def recognize_sales(
    image_bytes: bytes,
    *,
    file_name: str | None,
    doc_type: str = "sales",
    prompt_mode: str | None = None,
) -> RecognizeOutcome:
    """销售图识别主流程。doc_type 仅支持 sales（当前模板）。"""
    if doc_type not in SUPPORTED_DOC_TYPES:
        raise RecognizeError(f"单据类型暂不支持: {doc_type}（当前支持: {sorted(SUPPORTED_DOC_TYPES)}）")

    settings = get_settings()
    mode = prompt_mode or "cols8"
    instruction = prompt_builder.build_sales_instruction(mode)

    content_type = f"image/{file_name.rsplit('.', 1)[-1].lower()}" if file_name and "." in file_name else None
    try:
        outcome = await ocr_client.call_ocr(settings, image_bytes, content_type, instruction)
    except ocr_client.OcrCallError as exc:
        raise RecognizeError(f"OCR 调用失败: {exc}") from exc

    try:
        parsed = result_parser.parse_csv_rows(outcome.content)
    except result_parser.ContentParseError as exc:
        raise RecognizeError(f"OCR 内容解析失败: {exc}") from exc

    cleaned_rows, clean_warnings = cleaner.clean_sales_rows(parsed.rows)
    warnings = list(parsed.warnings) + clean_warnings
    if not cleaned_rows:
        raise RecognizeError("清洗后无有效数据行（模型返回内容见 raw）")

    path = csv_store.save_result_csv(
        rows=cleaned_rows,
        file_name=file_name,
        doc_type=doc_type,
        meta={
            "model": outcome.model_name,
            "latency_ms": outcome.latency_ms,
            "prompt_mode": mode,
            "warnings": warnings,
        },
    )
    return RecognizeOutcome(
        csv_path=path,
        file_name=file_name,
        doc_type=doc_type,
        rows=cleaned_rows,
        raw_content=outcome.content,
        warnings=warnings,
        model_name=outcome.model_name,
        latency_ms=outcome.latency_ms,
    )
