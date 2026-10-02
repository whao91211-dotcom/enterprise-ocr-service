"""Bound caller wait time without claiming cancellation of underlying work."""
from queue import Empty, Queue
from threading import BoundedSemaphore, Thread

_INFLIGHT = BoundedSemaphore(4)


class CallTimeout(TimeoutError):
    pass


def bounded_call(fn, seconds):
    if seconds <= 0:
        raise ValueError('调用等待期限必须大于0')
    if not _INFLIGHT.acquire(blocking=False):
        raise RuntimeError('仍有调用正在执行，请稍后重试并先核对执行状态')
    result = Queue(maxsize=1)
    def worker():
        try:
            result.put((True, fn()))
        except Exception as exc:
            result.put((False, exc))
        finally:
            _INFLIGHT.release()
    try:
        Thread(target=worker, daemon=True).start()
    except Exception:
        _INFLIGHT.release()
        raise
    try:
        success, value = result.get(timeout=seconds)
    except Empty as exc:
        raise CallTimeout('等待超时；底层操作可能仍在执行，不能据此认定已取消') from exc
    if not success:
        raise value
    return value


class ToolOutcome(str):
    def __new__(cls, value, failed=False):
        result = super().__new__(cls, value)
        result.failed = failed
        return result
