"""本地 mock OCR 服务器（OpenAI 兼容 /v1 协议）。

用途：9052 隧道未启动时的开发/联调替身，或向前端同事提供确定性的示例返回。
启动：python -m scripts.mock_ocr_server [--port 19052] [--model mock-ocr-vl]
返回内容可用环境变量覆盖：
  MOCK_OCR_CONTENT='{"text":"...","fields":{...}}'  （缺省为发票示例）
"""

from __future__ import annotations

import argparse
import json
import os

import uvicorn
from fastapi import FastAPI
from pydantic import BaseModel


class ChatMessage(BaseModel):
    role: str
    content: list | str


class ChatRequest(BaseModel):
    model: str = "mock-ocr-vl"
    messages: list[ChatMessage] = []
    temperature: float | None = None
    max_tokens: int | None = None
    response_format: dict | None = None


DEFAULT_CONTENT = json.dumps(
    {
        "text": "北京示例科技有限公司\n发票号码：1100234567\n开票日期：2025年1月8日\n"
        "销售方：示例贸易有限公司\n销售方税号：91110000MA01XXXXXX\n"
        "购买方：示例采购有限公司\n金额：1000.00\n税额：130.00\n价税合计：1130.00",
        "fields": {
            "invoice_code": "011002300511",
            "invoice_no": "1100234567",
            "invoice_date": "2025-01-08",
            "seller": "示例贸易有限公司",
            "seller_tax_no": "91110000MA01XXXXXX",
            "buyer": "示例采购有限公司",
            "amount": 1000.00,
            "tax": 130.00,
            "total": 1130.00,
        },
    },
    ensure_ascii=False,
)


def build_app() -> FastAPI:
    app = FastAPI(title="Mock OCR (OpenAI-compatible)", version="1.0")
    content = os.environ.get("MOCK_OCR_CONTENT", DEFAULT_CONTENT)

    @app.get("/v1/models")
    async def models():
        return {"object": "list", "data": [{"id": "mock-ocr-vl", "object": "model"}]}

    @app.post("/v1/chat/completions")
    async def chat(req: ChatRequest):
        # 可选调试：回显收到的 base64 图片前缀长度
        img_note = ""
        for msg in req.messages:
            if isinstance(msg.content, list):
                for part in msg.content:
                    if isinstance(part, dict) and part.get("type") == "image_url":
                        img_note = f"[image {len(part['image_url']['url'])} chars]"
        return {
            "id": "chatcmpl-mock",
            "object": "chat.completion",
            "model": req.model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            "note": img_note,
        }

    return app


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=int(os.environ.get("MOCK_OCR_PORT", "19052")))
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()
    uvicorn.run(build_app(), host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
