"""Простая очередь задач: один воркер в потоке. ТЗ 2.1.7, 6."""
import logging
import threading
import queue

from backend.services.pipeline import run_pipeline

logger = logging.getLogger(__name__)
_task_queue = queue.Queue()
_worker_started = False
_progress_subscribers = {}  # task_id -> list of callables(stage, progress)


def subscribe_progress(task_id: str, callback):
    if task_id not in _progress_subscribers:
        _progress_subscribers[task_id] = []
    _progress_subscribers[task_id].append(callback)


def unsubscribe_progress(task_id: str, callback):
    if task_id in _progress_subscribers:
        try:
            _progress_subscribers[task_id].remove(callback)
        except ValueError:
            pass


def _notify_progress(task_id: str, stage: str, progress: float):
    for cb in _progress_subscribers.get(task_id, []):
        try:
            cb(stage, progress)
        except Exception as e:
            logger.warning("progress callback error: %s", e)


def _worker():
    while True:
        try:
            task_id = _task_queue.get()
            if task_id is None:
                break
            logger.info("[worker] Взята задача из очереди: task_id=%s", task_id)
            try:
                run_pipeline(task_id, progress_cb=lambda s, p: _notify_progress(task_id, s, p))
            finally:
                _task_queue.task_done()
            from backend.database import get_connection
            try:
                with get_connection() as conn:
                    row = conn.execute("SELECT status, result_id FROM task WHERE id = ?", (task_id,)).fetchone()
                    if row:
                        logger.info("[worker] Задача %s обработана: status=%s, result_id=%s, очередь: %s", task_id, row["status"], row["result_id"] or "—", _task_queue.qsize())
                    else:
                        logger.info("[worker] Задача %s обработана, очередь: %s", task_id, _task_queue.qsize())
            except Exception:
                logger.info("[worker] Задача %s обработана, очередь: %s", task_id, _task_queue.qsize())
        except Exception as e:
            logger.exception("[worker] Ошибка воркера: %s", e)


def start_worker():
    global _worker_started
    if _worker_started:
        return
    _worker_started = True
    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    logger.info("Task worker started")


def enqueue(task_id: str):
    start_worker()
    _task_queue.put(task_id)


def get_queue_size() -> int:
    """Число задач в очереди (включая обрабатываемую)."""
    return _task_queue.qsize()
