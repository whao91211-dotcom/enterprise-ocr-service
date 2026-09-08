"""Pydantic API 契约（简化版：仅识别 API + 通用输出）。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class RecognizeOut(BaseModel):
    """POST /ocr/recognize 响应。"""

    ok: bool = True
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


class HealthOut(BaseModel):
    model_config = ConfigDict(extra="allow")

    status: str
    ocr_base_url: str
    ocr_model_configured: bool


class MessageOut(BaseModel):
    message: str
