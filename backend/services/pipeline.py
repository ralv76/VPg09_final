"""Единый пайплайн задачи: извлечение -> сценарий -> TTS -> музыка/обложка -> RSS. ТЗ 6, 2.2.2."""
import json
import logging
import os
import sqlite3
import uuid
from pathlib import Path
from datetime import datetime

from backend.config import STORAGE_PATH, MAX_TEXT_LENGTH, BASE_URL
from backend.database import get_connection
from backend.services.text_extraction import extract_from_pdf, extract_from_docx, extract_from_url
from backend.services.llm_client import generate_script
from backend.services.tts_client import generate_podcast_audio
from backend.services.music_cover import (
    list_music_tracks,
    pick_music_by_style,
    mix_voice_with_music,
    generate_cover_prompt,
    generate_cover_image,
)
from backend.services.rss_export import build_rss, write_id3, get_mp3_duration_seconds

logger = logging.getLogger(__name__)

STAGES = ["extract", "script", "tts", "music_cover", "rss", "done"]


def _update_task(task_id: str, status: str, stage: str = None, error_message: str = None, result_id: str = None, progress: int = None, activity_message: str = None):
    with get_connection() as conn:
        now = datetime.utcnow().isoformat()
        row = (status, stage or "", error_message or "", result_id or "", now, task_id)
        msg = (activity_message[:500] if activity_message else "") if activity_message is not None else None
        try:
            if progress is not None and msg is not None:
                conn.execute(
                    "UPDATE task SET status = ?, stage = ?, error_message = ?, result_id = ?, updated_at = ?, progress = ?, activity_message = ? WHERE id = ?",
                    (*row[:5], progress, msg, task_id),
                )
            elif progress is not None:
                conn.execute(
                    "UPDATE task SET status = ?, stage = ?, error_message = ?, result_id = ?, updated_at = ?, progress = ? WHERE id = ?",
                    (*row[:5], progress, task_id),
                )
            elif msg is not None:
                conn.execute(
                    "UPDATE task SET status = ?, stage = ?, error_message = ?, result_id = ?, updated_at = ?, activity_message = ? WHERE id = ?",
                    (*row[:5], msg, task_id),
                )
            else:
                conn.execute(
                    "UPDATE task SET status = ?, stage = ?, error_message = ?, result_id = ?, updated_at = ? WHERE id = ?",
                    row,
                )
        except sqlite3.OperationalError:
            if progress is not None:
                conn.execute(
                    "UPDATE task SET status = ?, stage = ?, error_message = ?, result_id = ?, updated_at = ?, progress = ? WHERE id = ?",
                    (*row[:5], progress, task_id),
                )
            else:
                conn.execute(
                    "UPDATE task SET status = ?, stage = ?, error_message = ?, result_id = ?, updated_at = ? WHERE id = ?",
                    row,
                )


def _get_task(task_id: str):
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM task WHERE id = ?", (task_id,)).fetchone()
        return dict(row) if row else None


def _ensure_session(session_id: str):
    with get_connection() as conn:
        conn.execute("INSERT OR IGNORE INTO session (id, created_at) VALUES (?, ?)", (session_id, datetime.utcnow().isoformat()))


def run_pipeline(task_id: str, progress_cb=None):
    """
    Выполнение пайплайна для задачи. progress_cb(stage, progress_0_1) опционально для WebSocket.
    """
    logger.info("[pipeline] Задача %s: старт", task_id)
    task = _get_task(task_id)
    if not task or task["status"] != "pending":
        logger.info("[pipeline] Задача %s: пропуск (нет задачи или status != pending)", task_id)
        return
    params = json.loads(task["params_json"] or "{}")
    session_id = task["session_id"]
    _update_task(task_id, "running", "extract", progress=0, activity_message="Подготовка…")

    try:
        # 1. Извлечение текста
        logger.info("[pipeline] Задача %s: этап 1 — извлечение текста", task_id)
        if progress_cb:
            progress_cb("extract", 0.0)
        _update_task(task_id, "running", "extract", progress=5, activity_message="Извлечение текста…")
        source = params.get("source", "file")
        text = ""
        if source == "url":
            url = params.get("url", "").strip()
            if not url:
                raise ValueError("Не указан URL")
            text, _, _ = extract_from_url(url)
        else:
            path = Path(params.get("file_path", ""))
            if not path.exists():
                raise ValueError("Файл не найден")
            if path.suffix.lower() == ".pdf":
                text, _, _ = extract_from_pdf(path)
            else:
                text, _, _ = extract_from_docx(path)
        if len(text) > MAX_TEXT_LENGTH:
            text = text[:MAX_TEXT_LENGTH]
        if progress_cb:
            progress_cb("extract", 1.0)
        _update_task(task_id, "running", "extract", progress=20, activity_message="Текст извлечён")
        logger.info("[pipeline] Задача %s: извлечение готово, символов: %s", task_id, len(text))

        # 2. Сценарий
        logger.info("[pipeline] Задача %s: этап 2 — генерация сценария", task_id)
        _update_task(task_id, "running", "script", progress=25, activity_message="Генерация сценария…")
        if progress_cb:
            progress_cb("script", 0.0)
        format_type = params.get("format", "dialog")
        style = params.get("style", "conversational")
        duration = params.get("duration", "standard")
        presentation = params.get("presentation", "neutral")
        script = generate_script(text, format_type=format_type, style=style, duration=duration, presentation=presentation)
        if progress_cb:
            progress_cb("script", 1.0)
        _update_task(task_id, "running", "script", progress=40, activity_message="Сценарий готов")
        logger.info("[pipeline] Задача %s: сценарий готов, реплик: %s", task_id, len(script))

        # 3. TTS
        logger.info("[pipeline] Задача %s: этап 3 — TTS (озвучка)", task_id)
        n_replicas = len(script)
        def on_replica_done(i: int, total: int):
            p = 45 + int(25 * i / total) if total else 45
            _update_task(task_id, "running", "tts", progress=min(p, 69), activity_message="Озвучка: реплика %d/%d" % (i, total))
        _update_task(task_id, "running", "tts", progress=45, activity_message="Синтез речи…")
        voice_map = params.get("voice_map") or {"1": "male_1", "2": "female_1"}
        voice_speed = float(params.get("voice_speed", 1.0))
        if voice_speed < 0.5 or voice_speed > 2.0:
            voice_speed = 1.0
        task_dir = STORAGE_PATH / task_id
        task_dir.mkdir(parents=True, exist_ok=True)
        voice_path = task_dir / "voice.mp3"
        generate_podcast_audio(
            script, voice_map, voice_path, speed=voice_speed,
            on_replica_done=on_replica_done,
            per_voice_dir=task_dir,
        )
        if progress_cb:
            progress_cb("tts", 1.0)
        _update_task(task_id, "running", "tts", progress=70, activity_message="Озвучка готова")
        logger.info("[pipeline] Задача %s: TTS готов", task_id)

        # 4. Музыка и обложка (музыка накладывается только при явном выборе music_id или "auto" по стилю)
        logger.info("[pipeline] Задача %s: этап 4 — музыка и обложка", task_id)
        _update_task(task_id, "running", "music_cover", progress=75, activity_message="Музыка и обложка…")
        music_path = None
        music_id = params.get("music_id")
        if music_id == "auto":
            music_id = pick_music_by_style(
                params.get("style", "conversational"),
                float(params.get("voice_speed", 1.0)),
            )
        if music_id:
            tracks = {t["id"]: Path(t["path"]) for t in list_music_tracks()}
            music_path = tracks.get(music_id)
        if not music_path or not music_path.exists():
            logger.info("[pipeline] Задача %s: музыка не выбрана — только голос", task_id)
        mixed_path = task_dir / "mixed.mp3"
        mix_voice_with_music(voice_path, music_path, mixed_path, params.get("music_volume_db", -20))
        cover_path = task_dir / "cover.jpg"
        try:
            prompt = generate_cover_prompt(text)
            custom = params.get("cover_prompt")
            img_bytes = generate_cover_image(prompt, custom_prompt=custom)
            cover_path.write_bytes(img_bytes)
            logger.info("[pipeline] Задача %s: обложка сгенерирована", task_id)
        except Exception as e:
            logger.warning("[pipeline] Задача %s: обложка не создана — %s", task_id, e)
        if progress_cb:
            progress_cb("music_cover", 1.0)
        _update_task(task_id, "running", "music_cover", progress=85, activity_message="Музыка и обложка готовы")

        # 5. RSS и метаданные
        logger.info("[pipeline] Задача %s: этап 5 — RSS и ID3", task_id)
        _update_task(task_id, "running", "rss", progress=90, activity_message="Финализация RSS и метаданных…")
        title = params.get("title") or text[:100].replace("\n", " ")
        description = params.get("description") or text[:500].replace("\n", " ")
        write_id3(mixed_path, title, cover_path if cover_path.exists() else None)
        duration_sec = get_mp3_duration_seconds(mixed_path)
        result_id = str(uuid.uuid4())
        rss_path = task_dir / "feed.xml"
        base_url = (BASE_URL or params.get("base_url") or "").strip().rstrip("/")
        mp3_url = f"{base_url}/api/files/{task_id}/mp3" if base_url else ""
        cover_url = f"{base_url}/api/files/{task_id}/cover" if base_url else ""
        rss_content = build_rss(title, description, mp3_url, cover_url, duration_sec, datetime.utcnow(), f"{base_url}/api/files/{task_id}/rss")
        rss_path.write_text(rss_content, encoding="utf-8")
        def _rel(p: Path) -> str:
            try:
                return p.relative_to(STORAGE_PATH).as_posix()
            except ValueError:
                return str(p)
        cover_rel = _rel(cover_path) if cover_path.exists() else ""
        with get_connection() as conn:
            conn.execute(
                "INSERT INTO result (id, task_id, mp3_path, cover_path, rss_path, title, description, duration_seconds, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (result_id, task_id, _rel(mixed_path), cover_rel, _rel(rss_path), title, description, duration_sec, datetime.utcnow().isoformat()),
            )
            logger.info("[pipeline] Задача %s: записываю в БД status=completed progress=100 result_id=%s", task_id, result_id)
            try:
                conn.execute(
                    "UPDATE task SET result_id = ?, status = ?, stage = ?, updated_at = ?, progress = ?, activity_message = ? WHERE id = ?",
                    (result_id, "completed", "done", datetime.utcnow().isoformat(), 100, "Готово", task_id),
                )
            except sqlite3.OperationalError:
                conn.execute(
                    "UPDATE task SET result_id = ?, status = ?, stage = ?, updated_at = ?, progress = ? WHERE id = ?",
                    (result_id, "completed", "done", datetime.utcnow().isoformat(), 100, task_id),
                )
            logger.info("[pipeline] Задача %s: БД обновлена (status=completed), следующий GET /api/tasks/%s должен вернуть completed", task_id, task_id)
        if progress_cb:
            progress_cb("rss", 1.0)
        logger.info(
            "[pipeline] Задача %s: завершена успешно | result_id=%s | mp3=%s | cover=%s | rss=%s | длительность=%s с",
            task_id, result_id, _rel(mixed_path), cover_rel or "(нет)", _rel(rss_path), duration_sec,
        )
    except Exception as e:
        err_msg = str(e)[:500]
        logger.exception("[pipeline] Задача %s: ошибка — %s", task_id, e)
        current = _get_task(task_id) or task
        _update_task(task_id, "failed", current.get("stage") or "tts", error_message=err_msg, activity_message="Ошибка: " + err_msg[:200])
