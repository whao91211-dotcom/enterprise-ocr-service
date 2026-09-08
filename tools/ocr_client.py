"""InternVL OCR 客户端：调 OpenAI 兼容端点(9052)识别图片 → 结构化行。

Prompt 采用 S1 实测固化的 cols8 中英语义引导：
  纯英文 key(desc,date,from...)会把 from 列错认成日期；
  中英语义(desc(顾客公司),date(发注日),from(发货公司/源公司)...)识别正确。
返回行键: desc/date/from/item/amount/price/tax/sum(字符串)。
"""

from __future__ import annotations

import base64
import csv
import io
from typing import Any

import httpx

import config

SYSTEM = "You are a helpful assistant."
INSTRUCTION = (
    "# 任务描述\n你需要对图像中的销售消息进行结构化信息提取，输出一个标准CSV格式的数据。"
    "\n每行输出 8 列，顺序固定为：desc(顾客公司),date(发注日),from(发货公司/源公司),"
    "item(货物名称),amount(货物数量),price(货物单价),tax(货物税率),sum(货物金额)。"
)

_HEADER_HINTS = ("desc", "date", "item", "amount", "price", "tax", "sum",
                 "顾客", "发注", "项目", "数量", "单价", "税率", "金额", "source")


class OcrError(Exception):
    pass


def _data_url(image_bytes: bytes, ext: str) -> str:
    mime = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
            "webp": "image/webp", "bmp": "image/bmp"}.get(ext.lower(), "image/png")
    return f"data:{mime};base64,{base64.b64encode(image_bytes).decode('ascii')}"


def recognize(image_bytes: bytes, ext: str = "png") -> dict[str, Any]:
    """识别一张图，返回 {"rows": [{8列}...], "raw": "...", "latency_ms": n}。"""
    url = config.OCR_BASE_URL.rstrip("/") + "/chat/completions"
    headers = {"Content-Type": "application/json"}
    if config.OCR_API_KEY:
        headers["Authorization"] = f"Bearer {config.OCR_API_KEY}"
    payload = {
        "model": config.OCR_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": INSTRUCTION},
                    {"type": "image_url", "image_url": {"url": _data_url(image_bytes, ext)}},
                ],
            },
        ],
        "temperature": config.OCR_TEMPERATURE,
        "max_tokens": config.OCR_MAX_TOKENS,
        "top_p": config.OCR_TOP_P,
    }
    try:
        import time

        start = time.monotonic()
        resp = httpx.post(url, headers=headers, json=payload,
                          timeout=config.OCR_TIMEOUT_SECONDS)
        latency = int((time.monotonic() - start) * 1000)
    except httpx.HTTPError as e:
        raise OcrError(f"OCR 调用失败: {e}") from e

    if resp.status_code != 200:
        raise OcrError(f"OCR 服务 HTTP {resp.status_code}: {resp.text[:300]}")
    data = resp.json()
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as e:
        raise OcrError(f"OCR 响应结构异常: {data}") from e

    rows = _parse_csv_rows(content)
    return {"rows": rows, "raw": content, "latency_ms": latency}


def _parse_csv_rows(content: str) -> list[dict[str, str]]:
    """解析模型返回的 CSV 文本为 8 列行。容错: 表头/空行/注释行跳过。"""
    rows: list[dict[str, str]] = []
    text = (content or "").strip()
    if not text:
        return rows
    reader = csv.reader(io.StringIO(text))
    keys = ["desc", "date", "from", "item", "amount", "price", "tax", "sum"]
    for line in reader:
        if not line or not any(c.strip() for c in line):
            continue
        if line[0].lstrip().startswith("#"):
            continue
        cells = [c.strip() for c in line]
        joined = "".join(cells).lower()
        if not joined or any(h in joined for h in _HEADER_HINTS) and len(cells) < 8:
            # 疑似表头(且不足 8 列)跳过
            continue
        row = {keys[i]: (cells[i] if i < len(cells) else "") for i in range(len(keys))}
        if any(row.values()):
            rows.append(row)
    return rows
