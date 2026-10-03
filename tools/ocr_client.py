"""双线路 OCR 客户端：InternVL CSV / Qwen-OCR JSON → 统一八字段行。

Prompt 采用 S1 实测固化的 cols8 中英语义引导：
  纯英文 key(desc,date,from...)会把 from 列错认成日期；
  中英语义(desc(顾客公司),date(发注日),from(发货公司/源公司)...)识别正确。
原 InternVL 提示与 CSV 契约保留；云端通过独立配置明确选择。
返回行键: desc/date/from/item/amount/price/tax/sum(字符串)。
"""

from __future__ import annotations

import base64
import csv
import io
import json
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
    provider = config.OCR_PROVIDER
    if provider not in ('internvl', 'qwen'):
        raise OcrError('OCR_PROVIDER 仅支持 internvl 或 qwen')
    cloud = provider == 'qwen'
    key = config.QWEN_OCR_API_KEY if cloud else config.OCR_API_KEY
    if cloud and not key:
        raise OcrError('QWEN_OCR_API_KEY 未配置')
    base_url = config.QWEN_OCR_BASE_URL if cloud else config.OCR_BASE_URL
    model = config.QWEN_OCR_MODEL if cloud else config.OCR_MODEL
    url = base_url.rstrip("/") + "/chat/completions"
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    instruction = INSTRUCTION
    if cloud:
        instruction = (
            '从销售单据图片逐行提取业务明细。只输出合法JSON对象 {"rows":[...]}，不要代码块或解释。'
            '每行必须包含以下8个键，值为原文字符串，缺失用空字符串：'
            'desc(顾客公司), date(发注日), from(发货公司/源公司), item(货物名称), '
            'amount(数量), price(单价), tax(税率), sum(票面金额)。'
            '保留原文语言、负号和折扣，不根据数量和单价重算金额，不猜测模糊字段。'
            '不要将表头、总计或说明当作商品明细；没有明细时返回 {"rows":[]}。'
        )
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": instruction},
                    {"type": "image_url", "image_url": {"url": _data_url(image_bytes, ext)}},
                ],
            },
        ],
        "temperature": 0 if cloud else config.OCR_TEMPERATURE,
        "max_tokens": config.QWEN_OCR_MAX_TOKENS if cloud else config.OCR_MAX_TOKENS,
        "top_p": config.OCR_TOP_P,
    }
    try:
        import time

        start = time.monotonic()
        resp = httpx.post(url, headers=headers, json=payload,
                          timeout=config.QWEN_OCR_TIMEOUT_SECONDS if cloud else config.OCR_TIMEOUT_SECONDS)
        latency = int((time.monotonic() - start) * 1000)
    except httpx.HTTPError as e:
        raise OcrError(f"OCR 调用失败: {type(e).__name__}") from e

    if resp.status_code != 200:
        raise OcrError(f"OCR 服务 HTTP {resp.status_code}（{provider}），请检查鉴权、模型权限或服务状态")
    try:
        data = resp.json()
        choice = data['choices'][0]
        content = choice['message']['content']
        if not isinstance(content, str):
            raise TypeError('content must be text')
        if choice.get('finish_reason') == 'length':
            raise OcrError('OCR 输出被截断，本次结果不入库；请减少图片内容或增加输出预算')
    except (ValueError, KeyError, IndexError, TypeError) as e:
        raise OcrError('OCR 响应结构异常，本次结果不入库') from e

    rows = _parse_json_rows(content) if cloud else _parse_csv_rows(content)
    return {"rows": rows, "raw": content, "latency_ms": latency,
            'provider': provider, 'model': model, 'usage': data.get('usage', {})}


def _parse_json_rows(content: str) -> list[dict[str, str]]:
    keys = {'desc', 'date', 'from', 'item', 'amount', 'price', 'tax', 'sum'}
    text = content.strip()
    if text.startswith('```') and text.endswith('```'):
        text = '\n'.join(text.splitlines()[1:-1])
    try:
        payload = json.loads(text)
        rows = payload['rows']
        if not isinstance(rows, list):
            raise ValueError('rows must be a list')
        parsed = []
        for row in rows:
            if not isinstance(row, dict) or set(row) != keys:
                raise ValueError('exactly eight fields required')
            if any(value is not None and not isinstance(value, str) for value in row.values()):
                raise ValueError('fields must be strings or null')
            normalized = {key: (value or '').strip() for key, value in row.items()}
            if not any(normalized.values()):
                raise ValueError('empty row')
            parsed.append(normalized)
        return parsed
    except (ValueError, KeyError, TypeError) as exc:
        raise OcrError('OCR 八字段JSON校验失败，本次结果不入库') from exc


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
