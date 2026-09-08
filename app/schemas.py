"""Pydantic API 契约（简化版：识别 + 人工确认）。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class RecognizeOut(BaseModel):
    """POST /ocr/recognize 响应。"""

    ok: bool = True
    skipped: bool = False  # 已确认图且未 force → true
    message: str = ""  # skipped 时的说明
    doc_type: str
    file_name: str | None = None
    csv_file: str = ""  # 落盘文件名
    csv_path: str = ""  # 落盘绝对路径
    row_count: int = 0
    rows: list[dict[str, Any]] = Field(default_factory=list)  # 清洗后 8 列
    columns: list[str] = Field(default_factory=list)  # 固定 8 列 key
    warnings: list[str] = Field(default_factory=list)
    model: str = ""
    latency_ms: int = 0
    raw: str | None = None  # 模型原始返回（调试用；默认不返回）


class ConfirmIn(BaseModel):
    """把某张图标记为已确认（人工核对/修改完成后调用）。"""

    source_file: str = Field(min_length=1, description="all_sales.csv 中的源图片文件名")
    reviewer: str = Field(default="manual", description="修改人标识（默认 manual）")


class ConfirmOut(BaseModel):
    ok: bool = True
    source_file: str
    confirmed_rows: int = 0  # 置为已确认的行数


class SourceStatus(BaseModel):
    source_file: str
    row_count: int
    status: str  # 待确认 / 已确认
    recognized_at: str = ""
    modified_by: str = ""
    modified_at: str = ""


class SourcesOut(BaseModel):
    total_sources: int
    pending: int  # 待确认图数
    confirmed: int  # 已确认图数
    items: list[SourceStatus]


class HealthOut(BaseModel):
    model_config = ConfigDict(extra="allow")

    status: str
    ocr_base_url: str
    ocr_model_configured: bool


class MessageOut(BaseModel):
    message: str
