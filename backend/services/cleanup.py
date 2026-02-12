"""Удаление по расписанию: файлы, метаданные задач и логи по срокам из конфига. ТЗ 5.2."""
import logging
from datetime import datetime, timedelta
from pathlib import Path

from backend.config import (
    STORAGE_PATH,
    FILE_RETENTION_DAYS,
    TASK_METADATA_DAYS,
    LOG_RETENTION_DAYS,
    BASE_DIR,
)
from backend.database import get_connection

logger = logging.getLogger(__name__)


def run_retention_cleanup() -> dict:
    """
    Удаляет файлы и записи старше сроков из конфига.
    Возвращает счётчики: удалённые каталоги задач, записи task/result, файлы логов.
    """
    stats = {"task_dirs": 0, "task_records": 0, "log_files": 0}
    file_cutoff = datetime.utcnow() - timedelta(days=FILE_RETENTION_DAYS)
    meta_cutoff = datetime.utcnow() - timedelta(days=TASK_METADATA_DAYS)
    log_cutoff = datetime.utcnow() - timedelta(days=LOG_RETENTION_DAYS)

    # 1. Удалить каталоги задач в storage старше FILE_RETENTION_DAYS
    if STORAGE_PATH.exists():
        for p in STORAGE_PATH.iterdir():
            if not p.is_dir():
                continue
            try:
                mtime = datetime.fromtimestamp(p.stat().st_mtime)
                if mtime < file_cutoff:
                    for f in p.rglob("*"):
                        if f.is_file():
                            f.unlink(missing_ok=True)
                    for f in sorted(p.rglob("*"), key=lambda x: len(x.parts), reverse=True):
                        if f.is_dir():
                            f.rmdir()
                    p.rmdir()
                    stats["task_dirs"] += 1
            except OSError as e:
                logger.warning("[cleanup] Не удалось удалить каталог %s: %s", p, e)

    # 2. Удалить записи task и result старше TASK_METADATA_DAYS (только завершённые/ошибка/отмена)
    with get_connection() as conn:
        cursor = conn.execute(
            "SELECT id, result_id FROM task WHERE updated_at < ? AND status IN ('completed', 'failed', 'cancelled')",
            (meta_cutoff.isoformat(),),
        )
        old_tasks = cursor.fetchall()
        for row in old_tasks:
            try:
                if row["result_id"]:
                    conn.execute("DELETE FROM result WHERE id = ?", (row["result_id"],))
                conn.execute("DELETE FROM task WHERE id = ?", (row["id"],))
                stats["task_records"] += 1
            except Exception as e:
                logger.warning("[cleanup] Удаление записи task %s: %s", row["id"], e)

    # 3. Удалить старые логи (файлы в logs/ старше LOG_RETENTION_DAYS)
    log_dir = BASE_DIR / "logs"
    if log_dir.exists():
        for p in log_dir.iterdir():
            if p.is_file():
                try:
                    mtime = datetime.fromtimestamp(p.stat().st_mtime)
                    if mtime < log_cutoff:
                        p.unlink(missing_ok=True)
                        stats["log_files"] += 1
                except OSError as e:
                    logger.warning("[cleanup] Не удалось удалить лог %s: %s", p, e)

    logger.info("[cleanup] Выполнено: каталогов задач=%s, записей БД=%s, файлов логов=%s", stats["task_dirs"], stats["task_records"], stats["log_files"])
    return stats
