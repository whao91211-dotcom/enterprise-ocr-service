"""OCR Tool：把已部署的识别服务(默认 http://127.0.0.1:8000)封装为智能体可调用的工具。

智能体通过本 Tool 上传图片路径 → 服务端识别 → 返回 8 列 rows + 写入 all_sales.csv(待确认)。
也提供 confirm 帮助，把识别结果直接置为已确认（可选，便于演示）。
"""

from __future__ import annotations

from typing import Any

import httpx

OCR_SERVICE_URL = "http://127.0.0.1:8000"


def recognize_image_bytes(
    image_bytes: bytes,
    file_name: str,
    *,
    doc_type: str = "sales",
    force: bool = False,
    include_raw: bool = False,
    timeout: float = 300.0,
) -> dict[str, Any]:
    """把图片字节发给 OCR 服务识别。返回响应 JSON(含 rows/csv_path)。"""
    url = f"{OCR_SERVICE_URL}/api/v1/ocr/recognize"
    files = {"file": (file_name, image_bytes, "application/octet-stream")}
    data = {
        "doc_type": doc_type,
        "force": "true" if force else "false",
        "include_raw": "true" if include_raw else "false",
    }
    with httpx.Client(timeout=timeout) as client:
        resp = client.post(url, files=files, data=data)
    if resp.status_code != 200:
        raise RuntimeError(f"OCR 服务返回 HTTP {resp.status_code}: {resp.text[:300]}")
    return resp.json()


def confirm_source(source_file: str, reviewer: str = "agent") -> dict[str, Any]:
    """把某图标记为已确认（人工核对后可调）。"""
    url = f"{OCR_SERVICE_URL}/api/v1/ocr/confirm"
    with httpx.Client(timeout=30) as client:
        resp = client.post(url, json={"source_file": source_file, "reviewer": reviewer})
    if resp.status_code not in (200, 404):
        raise RuntimeError(f"confirm 失败 HTTP {resp.status_code}: {resp.text[:300]}")
    return resp.json()


def _format_rows(rows: list[dict[str, Any]]) -> str:
    lines = []
    for i, r in enumerate(rows, start=1):
        lines.append(
            f"{i}. 顾客公司:{r.get('desc', '')} 发注日:{r.get('date', '')} "
            f"源公司:{r.get('from', '')} 项目:{r.get('item', '')} "
            f"数量:{r.get('amount', '')} 单价:{r.get('price', '')} "
            f"税率:{r.get('tax', '')} 金额:{r.get('sum', '')}"
        )
    return "\n".join(lines)
