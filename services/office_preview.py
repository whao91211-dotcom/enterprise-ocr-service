"""Serial, bounded preview conversion; generation is independent from preview."""
import os
import sys
import subprocess
from concurrent.futures import ThreadPoolExecutor
from threading import Lock, BoundedSemaphore
from pathlib import Path
from db import artifacts, database
from services.office_process import cleanup_owned

_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='office-preview')
_lock = Lock()
_active = set()
_slots = BoundedSemaphore(8)
PREVIEW_TIMEOUT_SECONDS = 60


def convert_artifact(ident):
    marker = None
    try:
        item = artifacts.get(ident)
        if item['kind'] not in ('docx','pptx'):
            return
        artifacts.preview_state(ident,'running')
        target = database._DATA_DIR/'previews'/f'{ident}.pdf'
        target.parent.mkdir(parents=True,exist_ok=True)
        target.unlink(missing_ok=True)
        marker = target.with_suffix('.process.json')
        marker.unlink(missing_ok=True)
        subprocess.run([sys.executable,'-m','services.office_worker',item['path'],str(target.resolve()),str(marker.resolve())],
            cwd=str(Path(__file__).resolve().parents[1]), timeout=PREVIEW_TIMEOUT_SECONDS, check=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        # Reject incomplete PDF output, including a stale file from an earlier attempt.
        import pypdfium2
        from services.pdf_runtime import PDF_LOCK
        with PDF_LOCK:
            pdf = pypdfium2.PdfDocument(str(target))
            try:
                if len(pdf)==0:
                    raise ValueError('Empty PDF')
            finally:
                pdf.close()
        artifacts.preview_state(ident,'ready',path=str(target.resolve()))
    except subprocess.TimeoutExpired:
        if marker:
            cleanup_owned(marker)
        artifacts.preview_state(ident,'failed','本机 Office 转换超时，可下载原文件或稍后重试。')
    except Exception:
        if marker:
            cleanup_owned(marker)
        artifacts.preview_state(ident,'failed','本机 Office 转换失败，请检查 Office 是否可用；PPT 预览前请关闭已打开的 PowerPoint。原文件仍可下载。')
    finally:
        if marker:
            marker.unlink(missing_ok=True)


def enqueue(ident):
    item = artifacts.get(ident)
    if item['kind'] in ('xlsx','png'):
        return
    if item['preview_status']=='ready' and item['preview_path'] and Path(item['preview_path']).is_file():
        return
    with _lock:
        if ident in _active:
            return
        if not _slots.acquire(blocking=False):
            raise RuntimeError('预览队列已满，请稍后重试')
        _active.add(ident)
        artifacts.preview_state(ident,'queued')
    def run():
        try:
            convert_artifact(ident)
        finally:
            with _lock:
                _active.discard(ident)
            _slots.release()
    try:
        _pool.submit(run)
    except Exception:
        with _lock:
            _active.discard(ident)
        _slots.release()
        artifacts.preview_state(ident,'failed','预览服务未启动')
        raise
