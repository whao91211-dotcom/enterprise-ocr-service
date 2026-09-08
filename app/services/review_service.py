"""审核工作流核心：approve / reject / reopen / retry，事务内完成状态迁移+版本+审计。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app import models as m
from app.models import DocStatus
from app.services import validation


class ReviewError(Exception):
    """审核业务错误（含用户可读 message 与 HTTP 状态建议）。"""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


@dataclass
class ReviewResult:
    status: str
    current_version: int
    new_version: int | None = None
    field_changes: list[dict[str, Any]] = field(default_factory=list)
    validation_errors: list[dict[str, str]] = field(default_factory=list)


def _diff_fields(old: dict[str, Any], new: dict[str, Any]) -> list[dict[str, Any]]:
    keys = set(old) | set(new)
    changes: list[dict[str, Any]] = []
    for k in sorted(keys):
        ov, nv = old.get(k), new.get(k)
        if ov != nv:
            changes.append({"key": k, "old": ov, "new": nv})
    return changes


def _diff_rows(old: list, new: list) -> list[dict[str, Any]]:
    """行级 diff：返回 [{row, key, old, new}]（仅记录发生变化的单元格）。"""
    changes: list[dict[str, Any]] = []
    for idx in range(max(len(old or []), len(new or []))):
        orow = old[idx] if idx < len(old or []) else {}
        nrow = new[idx] if idx < len(new or []) else {}
        for k in sorted(set(orow) | set(nrow)):
            if orow.get(k) != nrow.get(k):
                changes.append({"row": idx + 1, "key": k, "old": orow.get(k), "new": nrow.get(k)})
    return changes


async def approve_document(
    session: AsyncSession,
    document_id,
    *,
    reviewer: str,
    expected_version: int,
    corrections: dict[str, Any] | None,
    comment: str | None,
) -> ReviewResult:
    """审核通过：乐观锁校验 → 字段严格校验 → 追加 review 版本 → 审计留痕。"""
    doc = await session.get(m.Document, document_id, with_for_update=True)
    if doc is None:
        raise ReviewError("文档不存在", 404)
    if doc.status != DocStatus.PENDING_REVIEW:
        raise ReviewError(f"文档状态为 {doc.status}，仅 pending_review 可 approve", 409)
    if doc.current_version != expected_version:
        raise ReviewError(
            f"文档已被他人修改(期望版本 {expected_version}，当前 {doc.current_version})，请刷新后重试",
            409,
        )

    template = await m.get_template(session, doc.doc_type)
    field_defs = (template.field_defs if template else []) or []
    row_mode = bool(template.row_mode if template else False)

    corrections = corrections or {}
    prev = await m.get_latest_version(session, document_id)
    prev_fields: dict[str, Any] = (prev.fields if prev else {}) or {}
    prev_text: str | None = prev.text if prev else None

    new_fields_raw = corrections.get("fields")
    new_text = corrections.get("text")
    if new_fields_raw is None:
        new_fields_raw = dict(prev_fields)
    if new_text is None:
        new_text = prev_text

    if row_mode:
        # 多行记录契约：corrections.fields = {"rows": [ {...}, ... ]}
        rows = new_fields_raw.get("rows") if isinstance(new_fields_raw, dict) else None
        if rows is None and isinstance(new_fields_raw, list):
            rows = new_fields_raw
        vout = validation.validate_rows(rows, field_defs, strict=True)
        stored_fields = {"rows": vout.cleaned if vout.cleaned else (rows or [])}
        prev_rows = prev_fields.get("rows", []) if isinstance(prev_fields, dict) else []
        changes = _diff_rows(prev_rows, vout.cleaned if vout.cleaned else (rows or []))
    else:
        vout = validation.validate_fields(new_fields_raw, field_defs, strict=True)
        stored_fields = vout.cleaned if vout.cleaned else new_fields_raw
        changes = _diff_fields(prev_fields, vout.cleaned if vout.cleaned else new_fields_raw)

    if not vout.ok:
        result = ReviewResult(
            status=doc.status,
            current_version=doc.current_version,
            validation_errors=vout.error_dicts(),
        )
        raise ReviewValidationFailed(result)

    new_version_no = doc.current_version + 1
    cv = m.ContentVersion(
        document_id=doc.id,
        version_no=new_version_no,
        kind=m.VersionKind.REVIEW,
        text=new_text,
        fields=stored_fields,
        editor=reviewer,
        comment=comment,
    )
    session.add(cv)

    doc.current_version = new_version_no
    doc.status = DocStatus.APPROVED
    doc.reviewed_at = m.now()
    doc.latest_approved_at = m.now()
    doc.error_message = None

    session.add(
        m.ReviewAction(
            document_id=doc.id,
            action=m.ReviewActionKind.APPROVE,
            from_status=DocStatus.PENDING_REVIEW,
            to_status=DocStatus.APPROVED,
            reviewer=reviewer,
            comment=comment,
            field_changes=changes or None,
        )
    )
    await session.flush()
    return ReviewResult(
        status=DocStatus.APPROVED,
        current_version=doc.current_version,
        new_version=new_version_no,
        field_changes=changes,
    )


class ReviewValidationFailed(Exception):
    def __init__(self, result: ReviewResult):
        self.result = result


async def reject_document(
    session: AsyncSession,
    document_id,
    *,
    reviewer: str,
    comment: str | None,
) -> ReviewResult:
    doc = await session.get(m.Document, document_id, with_for_update=True)
    if doc is None:
        raise ReviewError("文档不存在", 404)
    if doc.status != DocStatus.PENDING_REVIEW:
        raise ReviewError(f"文档状态为 {doc.status}，仅 pending_review 可 reject", 409)
    if not comment:
        raise ReviewError("打回(reject)必须提供 comment 原因", 422)

    doc.status = DocStatus.REJECTED
    doc.reviewed_at = m.now()
    doc.error_message = None
    session.add(
        m.ReviewAction(
            document_id=doc.id,
            action=m.ReviewActionKind.REJECT,
            from_status=DocStatus.PENDING_REVIEW,
            to_status=DocStatus.REJECTED,
            reviewer=reviewer,
            comment=comment,
        )
    )
    await session.flush()
    return ReviewResult(status=DocStatus.REJECTED, current_version=doc.current_version)


async def reopen_document(
    session: AsyncSession,
    document_id,
    *,
    reviewer: str,
    comment: str | None,
) -> ReviewResult:
    """approved/rejected → pending_review 返修。"""
    doc = await session.get(m.Document, document_id, with_for_update=True)
    if doc is None:
        raise ReviewError("文档不存在", 404)
    if doc.status not in (DocStatus.APPROVED, DocStatus.REJECTED):
        raise ReviewError(f"文档状态为 {doc.status}，仅 approved/rejected 可 reopen", 409)

    from_status = doc.status
    doc.status = DocStatus.PENDING_REVIEW
    doc.error_message = None
    session.add(
        m.ReviewAction(
            document_id=doc.id,
            action=m.ReviewActionKind.REOPEN,
            from_status=from_status,
            to_status=DocStatus.PENDING_REVIEW,
            reviewer=reviewer,
            comment=comment,
        )
    )
    await session.flush()
    return ReviewResult(status=DocStatus.PENDING_REVIEW, current_version=doc.current_version)
