"""OCR 返回内容解析（简化版：销售单据 CSV 多行）。

模型按训练契约返回无表头多行 CSV（每行 8 列，逗号分隔）。
本模块容错解析：容忍引号/空白/少量说明行/表头混入；解析失败抛 ContentParseError。
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field

# 可能是表头的行（首行含这些词则视为表头丢弃）
_HEADER_HINTS = ("desc", "date", "item", "amount", "price", "tax", "sum", "顾客", "发注", "项目", "数量", "单价", "税率", "金额", "source", "company")


@dataclass
class ParsedResult:
    rows: list[list[str]] = field(default_factory=list)  # 每行一个单元格列表
    warnings: list[str] = field(default_factory=list)
    raw_text: str = ""


class ContentParseError(Exception):
    """模型返回内容无法解析为任何行。"""


def _looks_like_header(cells: list[str]) -> bool:
    joined = "".join(cells).strip().lower()
    if not joined:
        return True
    return any(h in joined for h in _HEADER_HINTS)


def parse_csv_rows(content: str) -> ParsedResult:
    """把模型返回的 CSV 文本解析为行。返回 ParsedResult(rows)。

    - 丢弃空行、注释(# 开头)、以及判定为表头的行；
    - 其余每行用 csv.reader 解析（支持引号包裹的逗号）。
    """
    result = ParsedResult(raw_text=content or "")
    text = (content or "").strip()
    if not text:
        raise ContentParseError("模型返回内容为空")

    reader = csv.reader(io.StringIO(text))
    for line_no, cells in enumerate(reader, start=1):
        if not cells or not any(c.strip() for c in cells):
            continue
        if cells[0].lstrip().startswith("#"):
            continue
        stripped = [c.strip() for c in cells]
        if _looks_like_header(stripped):
            result.warnings.append(f"第{line_no}行疑似表头，已跳过: {','.join(stripped)}")
            continue
        if len(stripped) < 2:  # 无法构成记录
            result.warnings.append(f"第{line_no}行字段过少，已跳过: {','.join(stripped)}")
            continue
        result.rows.append(stripped)

    if not result.rows:
        raise ContentParseError("模型返回内容中无有效数据行")
    return result


def parse_content(content: str) -> ParsedResult:  # 兼容旧名：销售 CSV 契约入口
    return parse_csv_rows(content)
