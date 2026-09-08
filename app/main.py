"""FastAPI 应用工厂（简化版）：识别 API + 健康检查，无数据库依赖。"""

from __future__ import annotations

import logging

from fastapi import FastAPI

from app.core.config import get_settings

logger = logging.getLogger(__name__)


def _build_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="销售单据识别 → CSV 落盘服务",
        description=(
            "上传销售单据图片 → InternVL(OpenAI 兼容, 本地 9052) 识别 "
            "→ 8 列清洗(desc,date,from,item,amount,price,tax,sum) → 落盘 output/ 每图一个 csv。"
        ),
        version="0.3.0",
    )

    from app.api.routes import health, ocr

    app.include_router(ocr.router, prefix=settings.api_prefix)
    # 健康检查同时暴露根路径与带前缀路径（运维探活无需知道前缀）
    app.include_router(health.router)
    app.include_router(health.router, prefix=settings.api_prefix)

    logger.info("应用启动 %s (ocr=%s model=%s)", settings.app_name, settings.ocr_base_url, settings.ocr_model)
    return app


app = _build_app()
