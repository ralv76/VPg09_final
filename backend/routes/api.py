"""REST API routes. ТЗ 4.2."""
import json
import logging
import threading
import uuid
from pathlib import Path

import requests
from flask import Blueprint, jsonify, request, send_file

from backend.config import (
    UPLOAD_PATH, MAX_TEXT_LENGTH, MAX_FILE_SIZE_BYTES, STORAGE_PATH,
    resolve_storage_path, BASE_URL,
    OPENAPI_LLM_URL, OPENAPI_LLM_API_KEY,
    OPENAPI_TTS_URL, OPENAPI_TTS_API_KEY,
    OPENAPI_IMAGE_URL, OPENAPI_IMAGE_API_KEY,
)
from backend.database import get_connection
from backend.services.text_extraction import (
    extract_from_pdf,
    extract_from_docx,
    extract_from_url,
)
from backend.services.llm_client import generate_script
from backend.services.music_cover import list_music_tracks
from backend.services.tts_client import list_voices, get_voice_preview_path, preload_voice_previews
from backend.tasks_queue import enqueue, get_queue_size

logger = logging.getLogger(__name__)
api_bp = Blueprint("api", __name__)

ALLOWED_EXTENSIONS = {"pdf", "docx", "doc"}


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def _stage_to_progress(stage):
    """Число 0–100 по этапу (fallback, если в БД нет колонки progress)."""
    s = (stage or "").lower()
    return {"extract": 15, "script": 40, "tts": 70, "music_cover": 85, "rss": 95, "done": 100}.get(s, 0)


@api_bp.route("/health")
def health():
    return jsonify({"status": "ok"})


@api_bp.route("/status")
def api_status():
    """
    Статус сервисов (LLM, TTS, генерация изображений) и очереди.
    Для отображения при загрузке страницы: подключение, доступность, очередь.
    """
    llm_configured = bool(OPENAPI_LLM_URL and OPENAPI_LLM_API_KEY)
    tts_configured = bool(OPENAPI_TTS_URL and OPENAPI_TTS_API_KEY)
    image_configured = bool(OPENAPI_IMAGE_URL and OPENAPI_IMAGE_API_KEY)
    queue_pending = get_queue_size()
    return jsonify({
        "llm": "configured" if llm_configured else "unavailable",
        "tts": "configured" if tts_configured else "unavailable",
        "image": "configured" if image_configured else "unavailable",
        "queue_pending": queue_pending,
    })


@api_bp.route("/extract", methods=["POST"])
def extract_text():
    """
    Извлечение текста: multipart file (key 'file') или JSON { "url": "..." }.
    ТЗ 2.1.1: валидация типа и размера, возврат очищенного текста.
    """
    try:
        if request.content_type and "multipart/form-data" in request.content_type:
            f = request.files.get("file")
            if not f or not f.filename:
                return jsonify({
                    "error": "Не выбран файл.",
                    "recommendation": "Загрузите PDF или DOCX (до 10 МБ) или укажите URL в JSON."
                }), 400
            if not allowed_file(f.filename):
                return jsonify({
                    "error": "Неподдерживаемый формат файла.",
                    "recommendation": "Используйте PDF или DOCX."
                }), 400
            UPLOAD_PATH.mkdir(parents=True, exist_ok=True)
            path = UPLOAD_PATH / f.filename
            f.save(path)
            try:
                if path.suffix.lower() == ".pdf":
                    text, removed_phones, removed_contacts = extract_from_pdf(path)
                else:
                    text, removed_phones, removed_contacts = extract_from_docx(path)
            finally:
                try:
                    path.unlink(missing_ok=True)
                except Exception:
                    pass
        elif request.is_json:
            data = request.get_json() or {}
            url = (data.get("url") or "").strip()
            if not url:
                return jsonify({
                    "error": "Не указан URL.",
                    "recommendation": "Передайте в теле запроса JSON: { \"url\": \"https://...\" }"
                }), 400
            text, removed_phones, removed_contacts = extract_from_url(url)
        else:
            return jsonify({
                "error": "Некорректный запрос.",
                "recommendation": "Отправьте файл (multipart/form-data, ключ 'file') или JSON с полем 'url'."
            }), 400

        if len(text) > MAX_TEXT_LENGTH:
            return jsonify({
                "error": f"Текст превышает лимит ({MAX_TEXT_LENGTH} символов).",
                "recommendation": "Сократите исходный материал или разбейте на части."
            }), 400

        removed = {}
        if removed_phones:
            removed["phones"] = removed_phones
        if removed_contacts:
            removed["contacts"] = removed_contacts
        return jsonify({
            "text": text,
            "length": len(text),
            "removed": removed,
        })
    except ValueError as e:
        logger.warning("extract_text validation: %s", e)
        return jsonify({
            "error": str(e),
            "recommendation": "Проверьте формат и размер файла или доступность URL."
        }), 400
    except requests.exceptions.RequestException as e:
        logger.warning("extract_text request: %s", e)
        return jsonify({
            "error": "Не удалось загрузить страницу по URL.",
            "recommendation": "Проверьте URL и доступность сайта."
        }), 400
    except Exception as e:
        logger.exception("extract_text error")
        return jsonify({
            "error": "Ошибка при извлечении текста.",
            "recommendation": "Проверьте формат файла или повторите попытку позже."
        }), 500


@api_bp.route("/script", methods=["POST"])
def create_script():
    """
    Генерация сценария (диалог двух ведущих). ТЗ 2.1.2.
    JSON: { "text": "...", "format": "dialog"|"monologue", "style": "...", "duration": "short"|"standard" }
    """
    if not request.is_json:
        return jsonify({
            "error": "Требуется JSON.",
            "recommendation": "Передайте text, format, style, duration в теле запроса."
        }), 400
    data = request.get_json() or {}
    text = (data.get("text") or "").strip()
    if not text:
        return jsonify({
            "error": "Поле 'text' обязательно.",
            "recommendation": "Добавьте извлечённый текст в запрос."
        }), 400
    format_type = (data.get("format") or "dialog").strip() or "dialog"
    style = (data.get("style") or "conversational").strip() or "conversational"
    duration = (data.get("duration") or "standard").strip() or "standard"
    presentation = (data.get("presentation") or "neutral").strip() or "neutral"
    try:
        script = generate_script(text, format_type=format_type, style=style, duration=duration, presentation=presentation)
        return jsonify({"script": script})
    except Exception as e:
        logger.exception("script generation error")
        return jsonify({
            "error": "Ошибка генерации сценария.",
            "recommendation": "Проверьте настройки LLM (OPENAPI_LLM_URL, OPENAPI_LLM_API_KEY) и повторите попытку."
        }), 500


def _now():
    from datetime import datetime
    return datetime.utcnow().isoformat()


@api_bp.route("/tasks", methods=["POST"])
def create_task():
    """
    Создание задачи: multipart (file + params) или JSON (url + params).
    Параметры: format, style, duration, voice_map, music_id, music_volume_db, title, description, cover_prompt, base_url.
    """
    session_id = request.headers.get("X-Session-Id") or request.args.get("session_id") or str(uuid.uuid4())
    task_id = str(uuid.uuid4())
    params = {}
    if request.content_type and "multipart/form-data" in request.content_type:
        f = request.files.get("file")
        if f and f.filename and allowed_file(f.filename):
            UPLOAD_PATH.mkdir(parents=True, exist_ok=True)
            task_upload = UPLOAD_PATH / task_id
            task_upload.mkdir(parents=True, exist_ok=True)
            path = task_upload / (f.filename or "document.pdf")
            f.save(path)
            params["source"] = "file"
            params["file_path"] = str(path)
        for key in ("format", "style", "duration", "presentation", "music_id", "title", "description", "cover_prompt", "base_url"):
            val = request.form.get(key)
            if val is not None:
                params[key] = val
        try:
            params["music_volume_db"] = float(request.form.get("music_volume_db", -20))
        except (TypeError, ValueError):
            params["music_volume_db"] = -20
        try:
            params["voice_speed"] = float(request.form.get("voice_speed", 1.0))
        except (TypeError, ValueError):
            params["voice_speed"] = 1.0
        voice_1 = request.form.get("voice_1") or "male_1"
        voice_2 = request.form.get("voice_2") or "female_1"
        params["voice_map"] = {"1": voice_1, "2": voice_2}
    elif request.is_json:
        data = request.get_json() or {}
        url = (data.get("url") or "").strip()
        if not url:
            return jsonify({
                "error": "Для создания по URL укажите поле 'url'.",
                "recommendation": "Передайте url в JSON."
            }), 400
        params["source"] = "url"
        params["url"] = url
        params["format"] = (data.get("format") or "dialog").strip() or "dialog"
        params["style"] = (data.get("style") or "conversational").strip() or "conversational"
        params["duration"] = (data.get("duration") or "standard").strip() or "standard"
        params["presentation"] = (data.get("presentation") or "neutral").strip() or "neutral"
        params["voice_map"] = data.get("voice_map") or {"1": "male_1", "2": "female_1"}
        params["voice_speed"] = float(data.get("voice_speed", 1.0)) if data.get("voice_speed") is not None else 1.0
        params["music_id"] = data.get("music_id")
        params["music_volume_db"] = data.get("music_volume_db", -20)
        params["title"] = data.get("title")
        params["description"] = data.get("description")
        params["cover_prompt"] = data.get("cover_prompt")
        params["base_url"] = (data.get("base_url") or request.url_root.rstrip("/")).strip()
    else:
        return jsonify({
            "error": "Отправьте файл (multipart) или JSON с полем url.",
            "recommendation": "См. документацию API."
        }), 400

    if not params.get("source"):
        return jsonify({"error": "Укажите файл или url.", "recommendation": "См. документацию API."}), 400

    with get_connection() as conn:
        conn.execute("INSERT OR IGNORE INTO session (id, created_at) VALUES (?, ?)", (session_id, _now()))
        conn.execute(
            "INSERT INTO task (id, session_id, status, stage, params_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (task_id, session_id, "pending", "", json.dumps(params), _now(), _now()),
        )
    enqueue(task_id)
    queue_pending = get_queue_size()
    payload = {
        "task_id": task_id,
        "session_id": session_id,
        "status": "pending",
        "queue_pending": queue_pending,
    }
    logger.info("[api] POST /tasks: создана задача task_id=%s, возвращаем payload=%s", task_id, payload)
    return jsonify(payload), 201


@api_bp.route("/podcasts")
def list_podcasts():
    """Список всех сгенерированных подкастов (completed) для страницы /podcasts."""
    try:
        limit = min(int(request.args.get("limit", 50)), 100)
    except (TypeError, ValueError):
        limit = 50
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT r.task_id, r.title, r.description, r.duration_seconds, r.created_at, r.cover_path
               FROM result r
               JOIN task t ON t.result_id = r.id
               WHERE t.status = 'completed'
               ORDER BY r.created_at DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()
    base = (BASE_URL or request.url_root.rstrip("/")).strip() or request.url_root.rstrip("/")
    out = []
    for row in rows:
        r = dict(row)
        r["url"] = f"{base}/result/{r['task_id']}"
        r["mp3_url"] = f"{base}/api/files/{r['task_id']}/mp3"
        r["cover_url"] = f"{base}/api/files/{r['task_id']}/cover" if r.get("cover_path") else None
        out.append(r)
    return jsonify({"podcasts": out})


@api_bp.route("/tasks/<task_id>")
def get_task(task_id):
    """Статус и результат задачи. ТЗ 4.2."""
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM task WHERE id = ?", (task_id,)).fetchone()
        if not row:
            logger.info("[api] GET task %s: 404 not found", task_id)
            return jsonify({"error": "Задача не найдена.", "recommendation": "Проверьте task_id."}), 404
        task = dict(row)
        result = None
        if task.get("result_id"):
            r = conn.execute("SELECT * FROM result WHERE id = ?", (task["result_id"],)).fetchone()
            if r:
                result = dict(r)
    progress_val = task["progress"] if "progress" in task and task.get("progress") is not None else _stage_to_progress(task.get("stage"))
    out = {
        "task_id": task_id,
        "status": task["status"],
        "stage": task.get("stage") or "",
        "progress": progress_val,
        "activity_message": task.get("activity_message") or "",
        "error_message": task.get("error_message"),
        "created_at": task["created_at"],
        "updated_at": task["updated_at"],
    }
    logger.info(
        "[api] GET task %s: status=%s stage=%s progress=%s has_result=%s",
        task_id, out["status"], out["stage"], out["progress"], result is not None,
    )
    if result:
        base = (BASE_URL or request.url_root.rstrip("/")).strip() or request.url_root.rstrip("/")
        out["result"] = {
            "title": result.get("title"),
            "description": result.get("description"),
            "duration_seconds": result.get("duration_seconds"),
            "mp3_url": f"{base}/api/files/{task_id}/mp3",
            "cover_url": f"{base}/api/files/{task_id}/cover" if result.get("cover_path") else None,
            "rss_url": f"{base}/api/files/{task_id}/rss",
        }
        logger.info("[api] GET task %s: возвращаем result_id=%s mp3=%s", task_id, result.get("id"), result.get("mp3_path"))
    resp = jsonify(out)
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    resp.headers["Pragma"] = "no-cache"
    return resp


@api_bp.route("/tasks/<task_id>/cancel", methods=["POST"])
def cancel_task(task_id):
    """Отмена задачи (помечаем отменённой; воркер может уже обрабатывать)."""
    with get_connection() as conn:
        cur = conn.execute("UPDATE task SET status = 'cancelled', updated_at = ? WHERE id = ? AND status IN ('pending', 'running')", (_now(), task_id))
        if cur.rowcount == 0:
            return jsonify({"error": "Задача не найдена или уже завершена.", "recommendation": "Проверьте task_id."}), 404
    return jsonify({"task_id": task_id, "status": "cancelled"})


@api_bp.route("/files/<task_id>/mp3")
def download_mp3(task_id):
    """Скачивание MP3. Доступ только через backend. ТЗ 3.7."""
    with get_connection() as conn:
        r = conn.execute("SELECT mp3_path FROM result r JOIN task t ON r.task_id = t.id WHERE t.id = ? AND t.status = 'completed'", (task_id,)).fetchone()
        if not r:
            return jsonify({"error": "Файл не найден."}), 404
        try:
            path = resolve_storage_path(r["mp3_path"])
        except Exception as e:
            logger.exception("resolve mp3_path for %s: %s", task_id, e)
            return jsonify({"error": "Ошибка пути к файлу."}), 500
        if not path.exists():
            return jsonify({"error": "Файл удалён."}), 404
        return send_file(str(path), as_attachment=True, download_name=f"podcast_{task_id}.mp3", mimetype="audio/mpeg")


@api_bp.route("/files/<task_id>/cover")
def download_cover(task_id):
    with get_connection() as conn:
        r = conn.execute("SELECT cover_path FROM result r JOIN task t ON r.task_id = t.id WHERE t.id = ? AND t.status = 'completed'", (task_id,)).fetchone()
        if not r or not r["cover_path"]:
            return jsonify({"error": "Обложка не найдена."}), 404
        try:
            path = resolve_storage_path(r["cover_path"])
        except Exception as e:
            logger.exception("resolve cover_path for %s: %s", task_id, e)
            return jsonify({"error": "Ошибка пути к обложке."}), 500
        if not path.exists():
            return jsonify({"error": "Файл удалён."}), 404
        return send_file(str(path), mimetype="image/jpeg")


@api_bp.route("/files/<task_id>/rss")
def download_rss(task_id):
    with get_connection() as conn:
        r = conn.execute("SELECT rss_path FROM result r JOIN task t ON r.task_id = t.id WHERE t.id = ? AND t.status = 'completed'", (task_id,)).fetchone()
        if not r:
            return jsonify({"error": "RSS не найден."}), 404
        try:
            path = resolve_storage_path(r["rss_path"])
        except Exception as e:
            logger.exception("resolve rss_path for %s: %s", task_id, e)
            return jsonify({"error": "Ошибка пути к RSS."}), 500
        if not path.exists():
            return jsonify({"error": "Файл удалён."}), 404
        return send_file(str(path), as_attachment=True, download_name=f"feed_{task_id}.xml", mimetype="application/rss+xml")


@api_bp.route("/music")
def list_music():
    """Список треков для выбора. ТЗ 2.1.5."""
    tracks = list_music_tracks()
    base = request.url_root.rstrip("/")
    for t in tracks:
        t["preview_url"] = f"{base}/api/music/preview/{t['id']}"
    return jsonify({"tracks": tracks})


@api_bp.route("/music/preview/<track_id>")
def music_preview(track_id):
    """Прослушать трек перед генерацией. ТЗ п.9."""
    tracks = {t["id"]: t for t in list_music_tracks()}
    if track_id not in tracks:
        return jsonify({"error": "Трек не найден."}), 404
    path = Path(tracks[track_id]["path"]).resolve()
    if not path.exists():
        return jsonify({"error": "Файл не найден.", "path": str(path)}), 404
    return send_file(str(path), mimetype="audio/mpeg")


@api_bp.route("/voices")
def voices_list():
    """Список голосов из модели/API. ТЗ п.6. При недоступности TTS — from_api: false и сообщение. Запускает фоновую подгрузку сэмплов."""
    voices, from_api = list_voices()
    base = request.url_root.rstrip("/")
    for v in voices:
        v["preview_url"] = f"{base}/api/voices/preview/{v['id']}"
    out = {"voices": voices, "from_api": from_api}
    if not from_api:
        out["message"] = "Список голосов с сервера TTS недоступен. Используются голоса по умолчанию. Для превью нужен рабочий TTS API или локальные сэмплы в static/voice_samples/"
    # Параллельная подгрузка сэмплов в фоне (фраза «Привет! Это я, голос: <название>», только если сэмпл ещё не сохранён)
    if voices:
        t = threading.Thread(target=preload_voice_previews, args=(voices,), daemon=True)
        t.start()
    return jsonify(out)


@api_bp.route("/voices/preview/<voice_id>")
def voice_preview(voice_id):
    """Превью голоса: локальный сэмпл или TTS (фраза «Привет! Это я, голос: <название>»). При ошибке — JSON с рекомендацией."""
    voices, _ = list_voices()
    voice_name = next((v["name"] for v in voices if v["id"] == voice_id), None)
    path = get_voice_preview_path(voice_id, voice_name=voice_name)
    if not path or not path.exists():
        return jsonify({
            "error": "Превью недоступно.",
            "recommendation": "Проверьте OPENAPI_TTS_URL и доступность TTS API. Либо положите файл в static/voice_samples/ (имя: <id>.mp3 или <модель>_<id>.mp3)."
        }), 404
    return send_file(path, mimetype="audio/mpeg")
