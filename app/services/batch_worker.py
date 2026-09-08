"""批量补识别队列：worker 轮询 ingest_queue 并复用单文档管线。

- 幂等：sha256 命中既有文档即完成该项（不重复 OCR）；
- 崩溃恢复：processing 超时项(>10 分钟)由 worker 收回为 queued；
- 人工重试：POST /queue/{id}/retry 将 failed 项重新入队。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update

from app import models as m
from app.core.db import get_session_factory
from app.services import ingest as ingest_svc
from app.services.pipeline import PipelineError, run_single_pipeline

logger = logging.getLogger(__name__)

PROCESSING_TIMEOUT = timedelta(minutes=10)


class BatchWorker:
    """应用内单协程队列消费者。生产量级上来后可平替为 Celery 等。"""

    def __init__(self, poll_interval: float = 2.0, page_size: int = 5):
        self.poll_interval = poll_interval
        self.page_size = page_size
        self._stop = asyncio.Event()

    def stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        logger.info("batch worker 启动 poll=%ss page=%s", self.poll_interval, self.page_size)
        while not self._stop.is_set():
            try:
                await self._process_page()
            except Exception:
                logger.exception("worker 处理一轮队列失败(下轮继续)")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.poll_interval)
            except TimeoutError:
                pass
        logger.info("batch worker 已停止")

    async def _process_page(self) -> None:
        factory = get_session_factory()
        async with factory() as session:
            # 收回超时 processing 项
            stale = datetime.now(UTC) - PROCESSING_TIMEOUT
            await session.execute(
                update(m.IngestQueueItem)
                .where(
                    m.IngestQueueItem.status == m.QueueStatus.PROCESSING,
                    m.IngestQueueItem.updated_at < stale,
                )
                .values(status=m.QueueStatus.QUEUED, error="processing 超时，自动回队")
            )
            await session.commit()

            res = await session.execute(
                select(m.IngestQueueItem)
                .where(m.IngestQueueItem.status == m.QueueStatus.QUEUED)
                .order_by(m.IngestQueueItem.id)
                .limit(self.page_size)
                .with_for_update(skip_locked=True)
            )
            items = list(res.scalars().all())
            if not items:
                return

            for item in items:
                item.status = m.QueueStatus.PROCESSING
                item.attempts += 1
                item.error = None
            await session.commit()

        for item in items:
            await self._process_item(item.id)

    async def _process_item(self, item_id: int) -> None:
        factory = get_session_factory()
        async with factory() as session:
            item = await session.get(m.IngestQueueItem, item_id, with_for_update=True)
            if item is None or item.status != m.QueueStatus.PROCESSING:
                return
            try:
                data, real_ext, display = await ingest_svc.fetch_bytes(item.source, item.original_ref)
                outcome = await run_single_pipeline(
                    session,
                    image_bytes=data,
                    real_ext=real_ext,
                    file_name=display,
                    content_type=f"image/{real_ext}",
                    ingest_source=item.source,
                    original_ref=item.original_ref,
                    doc_type=item.doc_type,
                    created_by="batch",
                )
                item.status = m.QueueStatus.DONE
                item.document_id = outcome.document.id
                item.error = None
                await session.commit()
            except (PipelineError, ingest_svc.IngestError) as exc:
                item.status = m.QueueStatus.FAILED
                item.error = str(exc)
                await session.commit()
            except Exception as exc:
                logger.exception("队列项 %s 处理异常", item_id)
                item.status = m.QueueStatus.FAILED
                item.error = f"内部异常: {exc!r}"
                await session.commit()


async def retry_queue_item(session, item_id: int) -> bool:
    """将 failed/queued 项重新置为 queued。"""
    item = await session.get(m.IngestQueueItem, item_id, with_for_update=True)
    if item is None:
        return False
    item.status = m.QueueStatus.QUEUED
    item.error = None
    await session.commit()
    return True


def start_batch_worker(poll_interval: float, page_size: int) -> BatchWorker:
    worker = BatchWorker(poll_interval=poll_interval, page_size=page_size)
    asyncio.get_running_loop().create_task(worker.run())
    return worker
