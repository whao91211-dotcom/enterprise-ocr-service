"""健康检查路由（简化版：不依赖数据库）。"""

from __future__ import annotations

from fastapi import APIRouter

from app.core.config import get_settings

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def healthz():
    settings = get_settings()
    return {
        "status": "ok",
        "ocr_base_url": settings.ocr_base_url,
        "ocr_model_configured": bool(settings.ocr_model),
        "output_dir": settings.output_dir,
    }
