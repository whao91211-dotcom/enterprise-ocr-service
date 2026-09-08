"""FastAPI 应用工厂：挂载全部路由、CORS、生命周期（批量 worker）。"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.core.config import get_settings
from app.core.db import get_session_factory, init_db

logger = logging.getLogger(__name__)


def _build_app() -> FastAPI:
    settings = get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        init_db()
        logger.info("应用启动 %s (db=%s)", settings.app_name, settings.database_url)
        # 本地开发(sqlite)自动建表；生产 PostgreSQL 用 alembic upgrade head
        if settings.database_url.startswith("sqlite"):
            from app import models as _m  # noqa: F401
            from app.core.db import Base

            def _create_all(sync_session):  # run_sync 需要同步回调
                Base.metadata.create_all(sync_session.get_bind())

            async with get_session_factory()() as _s:
                await _s.run_sync(_create_all)
                await _s.commit()
            from app.seed.seed_templates import ensure_default_templates

            await ensure_default_templates()
        if settings.batch_poll_interval_seconds > 0:
            from app.services.batch_worker import start_batch_worker

            worker = start_batch_worker(
                settings.batch_poll_interval_seconds, settings.batch_page_size
            )
            app.state.batch_worker = worker
            logger.info("批量队列 worker 已启动")
        yield
        worker = getattr(app.state, "batch_worker", None)
        if worker is not None:
            worker.stop()

    app = FastAPI(
        title="企业 OCR 识别→人工审核→RAG 管线服务",
        description=(
            "内部图片接入(在线上传/本地路径/URL/现有API) → 视觉模型 OCR(OpenAI 兼容, 本地 9052) "
            "→ 人工审核修正(版本链+审计) → 已审核数据经 /rag/data 增量交付 RAG。"
        ),
        version="0.1.0",
        lifespan=lifespan,
    )

    from app.api.routes import (
        documents,
        health,
        ingest,
        ocr,
        rag,
        review,
        templates,
    )

    for module in (ocr, ingest, documents, review, templates, rag):
        app.include_router(module.router, prefix=settings.api_prefix)
    # 健康检查同时暴露根路径与带前缀路径（运维探活无需知道前缀）
    app.include_router(health.router)
    app.include_router(health.router, prefix=settings.api_prefix)

    return app


app = _build_app()
