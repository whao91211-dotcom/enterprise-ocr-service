"""ORM 模型：单据模板 / 文档(识别任务) / OCR 调用 / 内容版本 / 审核审计 / 批量队列。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    select,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, jsonb_type

# 自增主键：PG 用 BIGSERIAL(BigInteger)，SQLite 必须 INTEGER PRIMARY KEY 才能自增
bigint_pk = BigInteger().with_variant(Integer(), "sqlite")

# ---------- 状态常量 ----------


class DocStatus:
    OCR_PENDING = "ocr_pending"  # 正在/等待调用 OCR
    OCR_FAILED = "ocr_failed"  # OCR 失败，可重试
    PENDING_REVIEW = "pending_review"  # OCR 成功，等待人工审核
    APPROVED = "approved"  # 审核通过，可供 RAG
    REJECTED = "rejected"  # 打回


DOC_LIFECYCLE = {
    DocStatus.OCR_PENDING: {DocStatus.OCR_FAILED, DocStatus.PENDING_REVIEW},
    DocStatus.OCR_FAILED: {DocStatus.OCR_PENDING},  # retry
    DocStatus.PENDING_REVIEW: {DocStatus.APPROVED, DocStatus.REJECTED},
    DocStatus.REJECTED: {DocStatus.PENDING_REVIEW},  # reopen 重审
    DocStatus.APPROVED: {DocStatus.PENDING_REVIEW},  # reopen 返修
}


class OcrRunStatus:
    SUCCESS = "success"
    FAILED = "failed"


class QueueStatus:
    QUEUED = "queued"
    PROCESSING = "processing"
    DONE = "done"
    FAILED = "failed"


class VersionKind:
    OCR = "ocr"  # 模型识别初值
    REVIEW = "review"  # 人工审核修正值


class ReviewActionKind:
    APPROVE = "approve"
    REJECT = "reject"
    REOPEN = "reopen"
    RETRY = "retry"


class IngestSource:
    MULTIPART = "multipart"
    LOCAL_PATH = "local_path"
    URL = "url"
    API = "api"


def now() -> datetime:
    # 全程 UTC 感知：asyncpg 绑定 timestamptz 要求 aware；sqlite 按 UTC 字符串存储，语义一致
    return datetime.now(UTC)


# ---------- 单据类型模板 ----------


class DocTemplate(Base):
    __tablename__ = "doc_templates"

    code: Mapped[str] = mapped_column(String(64), primary_key=True)  # e.g. invoice
    name: Mapped[str] = mapped_column(String(128))
    # field_defs: [{key,label,type:string|number|date|amount|enum|tel|id_card,required,regex?,enum?,unit?}]
    field_defs: Mapped[list[dict[str, Any]]] = mapped_column(jsonb_type(), default=list)
    row_mode: Mapped[bool] = mapped_column(Boolean, default=False)  # True=多行记录(CSV) 契约
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_by: Mapped[str] = mapped_column(String(64), default="system")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=now
    )


# ---------- 文档（一次识别任务的生命周期主体） ----------


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    doc_type: Mapped[str] = mapped_column(
        String(64), ForeignKey("doc_templates.code"), index=True
    )
    ingest_source: Mapped[str] = mapped_column(String(20), default=IngestSource.MULTIPART)
    original_ref: Mapped[str | None] = mapped_column(Text, nullable=True)  # 来源原址/路径
    file_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    content_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    storage_key: Mapped[str] = mapped_column(String(255))
    sha256: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(20), default=DocStatus.OCR_PENDING, index=True)
    current_version: Mapped[int] = mapped_column(Integer, default=0)  # 内容版本计数
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str] = mapped_column(String(64), default="system")
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    latest_approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=now, index=True
    )

    # 注意：不要设 lazy="joined" —— PG 的 SELECT ... FOR UPDATE 不能作用于 OUTER JOIN 的结果
    template: Mapped[DocTemplate | None] = relationship()
    ocr_runs: Mapped[list[OcrRun]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )
    content_versions: Mapped[list[ContentVersion]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )


# ---------- OCR 调用（原始存档，永不删除） ----------


class OcrRun(Base):
    __tablename__ = "ocr_runs"

    id: Mapped[int] = mapped_column(bigint_pk, primary_key=True, autoincrement=True)
    document_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("documents.id"), index=True
    )
    model_name: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(20), default=OcrRunStatus.SUCCESS)
    raw_response: Mapped[dict | None] = mapped_column(jsonb_type(), nullable=True)  # 模型完整返回
    parsed_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    parsed_fields: Mapped[dict[str, Any] | None] = mapped_column(jsonb_type(), nullable=True)
    parse_warnings: Mapped[list[str] | None] = mapped_column(jsonb_type(), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    document: Mapped[Document] = relationship(back_populates="ocr_runs")


# ---------- 内容版本快照（OCR 初值 + 人工修正值） ----------


class ContentVersion(Base):
    __tablename__ = "content_versions"
    __table_args__ = (UniqueConstraint("document_id", "version_no", name="uq_doc_version"),)

    id: Mapped[int] = mapped_column(bigint_pk, primary_key=True, autoincrement=True)
    document_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("documents.id"), index=True
    )
    version_no: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(10), default=VersionKind.OCR)
    text: Mapped[str | None] = mapped_column(Text, nullable=True)  # 全文（可人工修改）
    fields: Mapped[dict[str, Any]] = mapped_column(jsonb_type(), default=dict)  # 结构化字段终值
    editor: Mapped[str] = mapped_column(String(64), default="system")
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    document: Mapped[Document] = relationship(back_populates="content_versions")


# ---------- 审核审计日志 ----------


class ReviewAction(Base):
    __tablename__ = "review_actions"

    id: Mapped[int] = mapped_column(bigint_pk, primary_key=True, autoincrement=True)
    document_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("documents.id"), index=True
    )
    action: Mapped[str] = mapped_column(String(20), index=True)
    from_status: Mapped[str] = mapped_column(String(20))
    to_status: Mapped[str] = mapped_column(String(20))
    reviewer: Mapped[str] = mapped_column(String(64))
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    field_changes: Mapped[list[dict[str, Any]] | None] = mapped_column(
        jsonb_type(), nullable=True
    )  # [{key, old, new}]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ---------- 批量补识别队列 ----------


class IngestQueueItem(Base):
    __tablename__ = "ingest_queue"

    id: Mapped[int] = mapped_column(bigint_pk, primary_key=True, autoincrement=True)
    batch_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True, default=uuid.uuid4)
    source: Mapped[str] = mapped_column(String(20))  # local_path | url | api
    original_ref: Mapped[str] = mapped_column(Text)
    doc_type: Mapped[str] = mapped_column(String(64), ForeignKey("doc_templates.code"))
    status: Mapped[str] = mapped_column(String(20), default=QueueStatus.QUEUED, index=True)
    document_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("documents.id"), nullable=True
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=now
    )


# ---------- 便捷查询 ----------


async def get_latest_version(
    session, document_id: uuid.UUID, *, for_update: bool = False
) -> ContentVersion | None:
    stmt = (
        select(ContentVersion)
        .where(ContentVersion.document_id == document_id)
        .order_by(ContentVersion.version_no.desc())
        .limit(1)
    )
    if for_update:
        stmt = stmt.with_for_update()
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def get_template(session, code: str) -> DocTemplate | None:
    result = await session.execute(select(DocTemplate).where(DocTemplate.code == code))
    return result.scalar_one_or_none()
