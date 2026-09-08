"""initial schema

Revision ID: 0001
Revises:
Create Date: 2025-01-01

"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "doc_templates",
        sa.Column("code", sa.String(length=64), primary_key=True),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("field_defs", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("updated_by", sa.String(length=64), nullable=False, server_default="system"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_table(
        "documents",
        sa.Column("id", postgresql.UUID(), primary_key=True),
        sa.Column(
            "doc_type",
            sa.String(length=64),
            sa.ForeignKey("doc_templates.code"),
            nullable=False,
        ),
        sa.Column("ingest_source", sa.String(length=20), nullable=False),
        sa.Column("original_ref", sa.Text(), nullable=True),
        sa.Column("file_name", sa.String(length=255), nullable=True),
        sa.Column("content_type", sa.String(length=128), nullable=True),
        sa.Column("storage_key", sa.String(length=255), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False, unique=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("current_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(length=64), nullable=False, server_default="system"),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("latest_approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_documents_doc_type", "documents", ["doc_type"])
    op.create_index("ix_documents_sha256", "documents", ["sha256"])
    op.create_index("ix_documents_status", "documents", ["status"])
    op.create_index("ix_documents_updated_at", "documents", ["updated_at"])

    op.create_table(
        "ocr_runs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column("document_id", postgresql.UUID(), sa.ForeignKey("documents.id"), nullable=False),
        sa.Column("model_name", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("raw_response", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("parsed_text", sa.Text(), nullable=True),
        sa.Column("parsed_fields", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("parse_warnings", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_ocr_runs_document_id", "ocr_runs", ["document_id"])

    op.create_table(
        "content_versions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column("document_id", postgresql.UUID(), sa.ForeignKey("documents.id"), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=10), nullable=False),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("fields", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("editor", sa.String(length=64), nullable=False, server_default="system"),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("document_id", "version_no", name="uq_doc_version"),
    )
    op.create_index("ix_content_versions_document_id", "content_versions", ["document_id"])

    op.create_table(
        "review_actions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column("document_id", postgresql.UUID(), sa.ForeignKey("documents.id"), nullable=False),
        sa.Column("action", sa.String(length=20), nullable=False),
        sa.Column("from_status", sa.String(length=20), nullable=False),
        sa.Column("to_status", sa.String(length=20), nullable=False),
        sa.Column("reviewer", sa.String(length=64), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("field_changes", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_review_actions_document_id", "review_actions", ["document_id"])
    op.create_index("ix_review_actions_action", "review_actions", ["action"])

    op.create_table(
        "ingest_queue",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column("batch_id", postgresql.UUID(), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("original_ref", sa.Text(), nullable=False),
        sa.Column(
            "doc_type",
            sa.String(length=64),
            sa.ForeignKey("doc_templates.code"),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("document_id", postgresql.UUID(), sa.ForeignKey("documents.id"), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_ingest_queue_batch_id", "ingest_queue", ["batch_id"])
    op.create_index("ix_ingest_queue_status", "ingest_queue", ["status"])

    # 只读视图：最新已审核内容（供 RAG 同事直连库使用）
    op.execute(
        """
        CREATE VIEW v_rag_documents AS
        SELECT d.id AS document_id,
               d.doc_type,
               cv.version_no,
               cv.text,
               cv.fields,
               d.updated_at,
               cv.created_at AS reviewed_at
        FROM documents d
        JOIN content_versions cv ON cv.id = (
            SELECT v.id FROM content_versions v
            WHERE v.document_id = d.id
            ORDER BY v.version_no DESC
            LIMIT 1
        )
        WHERE d.status = 'approved'
        """
    )


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS v_rag_documents")
    op.drop_table("ingest_queue")
    op.drop_table("review_actions")
    op.drop_table("content_versions")
    op.drop_table("ocr_runs")
    op.drop_table("documents")
    op.drop_table("doc_templates")
