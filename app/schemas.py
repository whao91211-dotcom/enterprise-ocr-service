"""Pydantic API 契约（请求/响应）。"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# ---------- 单据模板 ----------


class FieldDef(BaseModel):
    key: str
    label: str
    type: Literal["string", "number", "date", "amount", "enum", "tel", "id_card"] = "string"
    required: bool = False
    regex: str | None = None
    enum: list[str] | None = None
    unit: str | None = None


class TemplateIn(BaseModel):
    code: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]*$")
    name: str
    field_defs: list[FieldDef] = Field(default_factory=list)
    active: bool = True


class TemplateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    code: str
    name: str
    field_defs: list[dict[str, Any]]
    active: bool
    updated_by: str
    updated_at: datetime | None = None


# ---------- 图片来源 ----------


class SourceRef(BaseModel):
    type: Literal["multipart", "local_path", "url", "api"]
    ref: str = ""  # local_path/url/api 时的路径或地址；multipart 忽略


class SingleSubmitIn(BaseModel):
    """对已有可访问图片直接触发单张识别。"""

    doc_type: str
    source: SourceRef
    original_ref: str | None = None


class ImportItemIn(BaseModel):
    source: Literal["local_path", "url", "api"]
    ref: str = Field(min_length=1)


class ImportIn(BaseModel):
    doc_type: str
    items: list[ImportItemIn] = Field(min_length=1, max_length=2000)


# ---------- 队列 ----------


class QueueItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    batch_id: uuid.UUID
    source: str
    original_ref: str
    doc_type: str
    status: str
    document_id: uuid.UUID | None = None
    error: str | None = None
    attempts: int
    updated_at: datetime | None = None


class ImportResult(BaseModel):
    batch_id: uuid.UUID
    accepted: int
    items: list[QueueItemOut]


class QueueProgress(BaseModel):
    batch_id: uuid.UUID
    total: int
    queued: int
    processing: int
    done: int
    failed: int
    items: list[QueueItemOut]


# ---------- OCR 运行 ----------


class OcrRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    model_name: str
    status: str
    parsed_text: str | None = None
    parsed_fields: dict[str, Any] | None = None
    parse_warnings: list[str] | None = None
    error: str | None = None
    latency_ms: int | None = None
    created_at: datetime | None = None


# ---------- 文档 ----------


class ContentVersionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    version_no: int
    kind: str
    text: str | None = None
    fields: dict[str, Any]
    editor: str
    comment: str | None = None
    created_at: datetime | None = None


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    doc_type: str
    template_name: str | None = None
    template_field_defs: list[dict[str, Any]] = Field(default_factory=list)
    ingest_source: str
    original_ref: str | None = None
    file_name: str | None = None
    content_type: str | None = None
    sha256: str
    status: str
    current_version: int
    error_message: str | None = None
    created_by: str
    reviewed_at: datetime | None = None
    latest_approved_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    latest_ocr: OcrRunOut | None = None
    latest_content: ContentVersionOut | None = None
    image_url: str | None = None  # 由路由填充（签名短时效）


class DocumentSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    doc_type: str
    template_name: str | None = None
    file_name: str | None = None
    status: str
    current_version: int
    created_at: datetime | None = None
    updated_at: datetime | None = None
    # 待审摘要预览（OCR 初值）
    preview_text: str | None = None
    preview_fields: dict[str, Any] | None = None


class ReviewQueueOut(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[DocumentSummary]


class PageOut(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[Any]


# ---------- 审核 ----------


class ReviewCorrections(BaseModel):
    """人工修正：fields 为完整字段终值（未提供则沿用上一版本）；text 可单独修正。"""

    fields: dict[str, Any] | None = None
    text: str | None = None


class ReviewIn(BaseModel):
    action: Literal["approve", "reject"]
    expected_version: int | None = None  # approve 必填（乐观锁）
    corrections: ReviewCorrections | None = None
    comment: str | None = None  # reject 必填原因 / approve 可填备注


class ReopenIn(BaseModel):
    comment: str | None = None


class ReviewOut(BaseModel):
    document_id: uuid.UUID
    status: str
    current_version: int
    new_version: int | None = None
    field_changes: list[dict[str, Any]] = Field(default_factory=list)


class AuditActionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    action: str
    from_status: str
    to_status: str
    reviewer: str
    comment: str | None = None
    field_changes: list[dict[str, Any]] | None = None
    created_at: datetime | None = None


class HistoryOut(BaseModel):
    versions: list[ContentVersionOut]
    audit: list[AuditActionOut]


class ValidationErrorDetail(BaseModel):
    field: str
    label: str
    message: str


# ---------- RAG ----------


class RagRow(BaseModel):
    document_id: uuid.UUID
    doc_type: str
    version_no: int
    text: str | None = None
    fields: dict[str, Any]
    updated_at: datetime | None = None
    reviewed_at: datetime | None = None


class RagDataOut(BaseModel):
    items: list[RagRow]
    next_cursor: str | None = None  # None 表示已到末页


class RagTombstone(BaseModel):
    document_id: uuid.UUID
    status: str
    latest_approved_at: datetime | None = None
    updated_at: datetime | None = None


class RagTombstonesOut(BaseModel):
    items: list[RagTombstone]
    next_cursor: str | None = None


# ---------- 提交结果 ----------


class SubmitOut(BaseModel):
    document_id: uuid.UUID
    status: str
    duplicate: bool = False
    message: str = ""
    document: DocumentOut | None = None


class MessageOut(BaseModel):
    message: str
