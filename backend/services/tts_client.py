"""Клиент TTS через кастомный OpenAPI-совместимый URL + API_KEY. ТЗ 4.2."""
import io
import hashlib
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import List, Dict, Optional, Callable

import httpx
from httpx import HTTPStatusError

try:
    from pydub import AudioSegment
except ImportError:
    AudioSegment = None

from backend.config import (
    OPENAPI_TTS_URL,
    OPENAPI_TTS_URL2,
    OPENAPI_TTS_API_KEY,
    OPENAPI_TTS_VOICES_LIST_URL,
    OPENAPI_TTS_MODEL,
    STORAGE_PATH,
    VOICE_SAMPLES_DIR,
)

logger = logging.getLogger(__name__)

# Голоса по умолчанию, если TTS вообще не настроен (ТЗ 3.3).
DEFAULT_VOICES = [
    {"id": "male_1", "name": "Мужской 1"},
    {"id": "male_2", "name": "Мужской 2"},
    {"id": "female_1", "name": "Женский 1"},
    {"id": "female_2", "name": "Женский 2"},
]
# Голоса OpenAI-стиля TTS (alloy, echo, nova…), когда URL синтеза есть, но отдельный список голосов API не отдаёт.
OPENAI_STYLE_VOICES = [
    {"id": "alloy", "name": "Alloy"},
    {"id": "echo", "name": "Echo"},
    {"id": "fable", "name": "Fable"},
    {"id": "onyx", "name": "Onyx"},
    {"id": "nova", "name": "Nova"},
    {"id": "shimmer", "name": "Shimmer"},
]
PREVIEW_PHRASE_DEFAULT = "Привет, это пример голоса."

CACHE_DIR = STORAGE_PATH / "tts_cache"
PREVIEW_CACHE_DIR = STORAGE_PATH / "tts_preview"
BITRATE_KBPS = 128

# Блокировки по voice_id, чтобы не дергать TTS параллельно для одного голоса
_preview_locks: Dict[str, threading.Lock] = {}
_preview_locks_lock = threading.Lock()


def _safe_voice_id(voice_id: str) -> str:
    """Безопасное имя файла: голоса из API часто по имени (alice, ermil)."""
    s = (voice_id or "").strip()
    for c in ("/", "\\", ":", "*", "?", '"', "<", ">", "|"):
        s = s.replace(c, "_")
    return s[:64] or "voice"


def _sample_file_key(voice_id: str, model: Optional[str] = None) -> str:
    """Имя файла сэмпла: с учётом модели (model_voice_id или voice_id)."""
    safe_voice = _safe_voice_id(voice_id)
    if not model or not model.strip():
        return safe_voice
    safe_model = _safe_voice_id(model.strip())
    return f"{safe_model}_{safe_voice}"


def _cache_key(text: str, voice_id: str, speed: float = 1.0) -> str:
    return hashlib.sha256((text.strip() + "|" + voice_id + "|" + str(speed)).encode()).hexdigest()


def _cached_path(key: str) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / f"{key}.mp3"


def get_cached_audio(text: str, voice_id: str, speed: float = 1.0) -> Optional[Path]:
    """Кэш по хешу текст+голос+скорость. ТЗ 8.1."""
    key = _cache_key(text, voice_id, speed)
    path = _cached_path(key)
    return path if path.exists() else None


def save_to_cache(text: str, voice_id: str, audio_bytes: bytes, speed: float = 1.0) -> Path:
    key = _cache_key(text, voice_id, speed)
    path = _cached_path(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    if AudioSegment is None:
        path.write_bytes(audio_bytes)
        return path
    try:
        seg = AudioSegment.from_file(io.BytesIO(audio_bytes))
        seg.export(str(path), format="mp3", bitrate=f"{BITRATE_KBPS}k")
    except Exception:
        path.write_bytes(audio_bytes)
    return path


def list_voices():
    """Получить список голосов из API или fallback. Пробует LIST_URL, TTS_URL/voices, TTS_URL2/voices.
    Если TTS настроен (URL + ключ), но ни один URL списка не сработал — возвращает голоса OpenAI-стиля (alloy, echo, nova…)."""
    urls = []
    if OPENAPI_TTS_VOICES_LIST_URL:
        urls.append(OPENAPI_TTS_VOICES_LIST_URL.strip())
    if OPENAPI_TTS_URL:
        urls.append(OPENAPI_TTS_URL.rstrip("/") + "/voices")
    if OPENAPI_TTS_URL2:
        urls.append(OPENAPI_TTS_URL2.rstrip("/") + "/voices")
    if not OPENAPI_TTS_API_KEY:
        return list(DEFAULT_VOICES), False
    if not urls:
        # TTS ключ есть, но нет ни одного URL для списка — используем голоса OpenAI-стиля
        if OPENAPI_TTS_URL or OPENAPI_TTS_URL2:
            return list(OPENAI_STYLE_VOICES), True
        return list(DEFAULT_VOICES), False
    for url in urls:
        try:
            with httpx.Client(timeout=15.0) as client:
                resp = client.get(url, headers={"Authorization": f"Bearer {OPENAPI_TTS_API_KEY}"})
                resp.raise_for_status()
                data = resp.json()
            if isinstance(data, list):
                voices = [
                    {"id": str(v.get("id", v.get("voice_id", i))), "name": str(v.get("name", v.get("id", f"Голос {i}")))}
                    for i, v in enumerate(data)
                ]
            else:
                raw = data.get("voices") or data.get("data") or []
                voices = [
                    {"id": str(v.get("id", v.get("voice_id", i))), "name": str(v.get("name", v.get("id", f"Голос {i}")))}
                    for i, v in enumerate(raw)
                ]
            if voices:
                return voices, True
        except Exception as e:
            logger.debug("list_voices %s failed: %s", url, e)
            continue
    # Все URL списка недоступны, но TTS настроен — показываем голоса OpenAI-стиля (подходят для /audio/speech)
    if OPENAPI_TTS_URL or OPENAPI_TTS_URL2:
        logger.info("list_voices: используем голоса OpenAI-стиля (alloy, echo, nova…)")
        return list(OPENAI_STYLE_VOICES), True
    return list(DEFAULT_VOICES), False


def _preview_phrase(voice_name: Optional[str] = None) -> str:
    """Фраза-образец для превью: «Привет! Это я, голос: <название>» или дефолт."""
    if voice_name and str(voice_name).strip():
        return f"Привет! Это я, голос: {voice_name.strip()}"
    return PREVIEW_PHRASE_DEFAULT


def _voice_preview_needs_download(voice_id: str, model: Optional[str] = None) -> bool:
    """Нужно ли качать сэмпл: нет ни локального файла, ни в кэше (учёт модели)."""
    key = _sample_file_key(voice_id, model)
    if VOICE_SAMPLES_DIR.exists():
        if (VOICE_SAMPLES_DIR / f"{key}.mp3").exists():
            return False
    PREVIEW_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    if (PREVIEW_CACHE_DIR / f"{key}.mp3").exists():
        return False
    return True


def get_voice_preview_path(
    voice_id: str,
    voice_name: Optional[str] = None,
    model: Optional[str] = None,
) -> Optional[Path]:
    """
    Превью голоса: локальный сэмпл или кэш, иначе TTS с фразой «Привет! Это я, голос: <название>».
    Имя файла: с учётом модели (model_voice_id.mp3 или voice_id.mp3). Запрос к TTS только если сэмпл ещё не сохранён.
    """
    key = _sample_file_key(voice_id, model or OPENAPI_TTS_MODEL)
    # 1) Локальные сохранённые сэмплы
    if VOICE_SAMPLES_DIR.exists():
        local_path = VOICE_SAMPLES_DIR / f"{key}.mp3"
        if local_path.exists():
            return local_path
    # 2) Кэш от предыдущей генерации через TTS
    PREVIEW_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = PREVIEW_CACHE_DIR / f"{key}.mp3"
    if path.exists():
        return path
    # 3) Один поток на голос — не дублируем запросы к TTS
    lock_key = f"{model or ''}_{voice_id}"
    with _preview_locks_lock:
        if lock_key not in _preview_locks:
            _preview_locks[lock_key] = threading.Lock()
    with _preview_locks[lock_key]:
        if path.exists():
            return path
        try:
            phrase = _preview_phrase(voice_name)
            audio_bytes = call_tts(phrase, voice_id, speed=1.0)
            path.write_bytes(audio_bytes)
            # Сохранить в static/voice_samples (правильные сэмплы с учётом модели)
            VOICE_SAMPLES_DIR.mkdir(parents=True, exist_ok=True)
            sample_path = VOICE_SAMPLES_DIR / f"{key}.mp3"
            sample_path.write_bytes(audio_bytes)
            return sample_path
        except Exception as e:
            logger.warning("voice preview failed for %s: %s", voice_id, e)
            return None


def preload_voice_previews(voices: List[Dict[str, str]]) -> None:
    """
    В фоне подгружает сэмплы для голосов, у которых ещё нет сэмпла. Для каждого — фраза «Привет! Это я, голос: <название>».
    voices: список {"id": "...", "name": "..."}. Сэмплы сохраняются с учётом модели (OPENAPI_TTS_MODEL).
    """
    model = OPENAPI_TTS_MODEL
    to_load = [v for v in voices if _voice_preview_needs_download(v["id"], model)]
    if not to_load:
        return

    def _load_one(v: Dict[str, str]) -> None:
        try:
            get_voice_preview_path(v["id"], voice_name=v.get("name"), model=model)
        except Exception as e:
            logger.debug("preload preview %s: %s", v.get("id"), e)

    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(_load_one, to_load))
    except Exception as e:
        logger.warning("preload_voice_previews: %s", e)


def call_tts(text: str, voice_id: str, speed: float = 1.0) -> bytes:
    """
    Вызов TTS API. speed: 0.5–2.0 (передаётся в API, если поддерживается).
    При ошибке по OPENAPI_TTS_URL пробует OPENAPI_TTS_URL2 (если задан).
    """
    urls = [u for u in (OPENAPI_TTS_URL, OPENAPI_TTS_URL2) if u]
    if not urls or not OPENAPI_TTS_API_KEY:
        raise RuntimeError("TTS не настроен: задайте OPENAPI_TTS_URL и OPENAPI_TTS_API_KEY")
    headers = {"Authorization": f"Bearer {OPENAPI_TTS_API_KEY}"}
    payload = {"input": text[:5000], "voice": voice_id}
    if OPENAPI_TTS_MODEL:
        payload["model"] = OPENAPI_TTS_MODEL
    if speed != 1.0:
        payload["speed"] = speed
    last_error = None
    with httpx.Client(timeout=60.0) as client:
        for url in urls:
            url = url.rstrip("/")
            try:
                resp = client.post(url, json=payload, headers=headers)
                resp.raise_for_status()
                content_type = (resp.headers.get("content-type") or "").lower()
                if "application/json" in content_type:
                    data = resp.json()
                    import base64
                    b64 = data.get("audio") or data.get("data")
                    if b64:
                        return base64.b64decode(b64)
                    raise ValueError("Ответ TTS: JSON без поля audio/data")
                return resp.content
            except (HTTPStatusError, httpx.RequestError, ValueError) as e:
                last_error = e
                logger.debug("TTS %s failed: %s, trying next URL", url, e)
                continue
    if last_error is None:
        last_error = RuntimeError("Нет доступных TTS URL")
    if isinstance(last_error, HTTPStatusError) and last_error.response.status_code == 404:
        raise RuntimeError(
            "TTS недоступен: оба URL вернули ошибку (по первому — 404). Укажите правильный OPENAPI_TTS_URL и при необходимости OPENAPI_TTS_URL2 (эндпоинты синтеза речи). Либо добавьте локальные сэмплы в static/voice_samples/ для превью."
        ) from last_error
    raise RuntimeError(f"TTS ошибка после попыток по всем URL: {last_error}") from last_error


def synthesize_replica(text: str, voice_id: str, use_cache: bool = True, speed: float = 1.0) -> Path:
    """Одна реплика: из кэша или вызов API. speed: 0.5–2.0."""
    if not text.strip():
        raise ValueError("Пустой текст реплики")
    if use_cache:
        cached = get_cached_audio(text, voice_id, speed)
        if cached:
            return cached
    audio_bytes = call_tts(text, voice_id, speed=speed)
    return save_to_cache(text, voice_id, audio_bytes, speed)


def concatenate_audio_segments(segments: List[Path], output_path: Path) -> Path:
    """Склейка сегментов в один MP3 128 kbps. ТЗ 3.3."""
    if AudioSegment is None:
        raise RuntimeError("pydub недоступен. Используйте Python 3.12 или установите pyaudioop.")
    if not segments:
        raise ValueError("Нет сегментов для склейки")
    combined = None
    for path in segments:
        seg = AudioSegment.from_file(str(path))
        if combined is None:
            combined = seg
        else:
            combined += seg
    combined.export(str(output_path), format="mp3", bitrate=f"{BITRATE_KBPS}k")
    return output_path


def generate_podcast_audio(
    script: List[Dict[str, str]],
    voice_map: Dict[str, str],
    output_path: Path,
    speed: float = 1.0,
    on_replica_done: Optional[Callable[[int, int], None]] = None,
    per_voice_dir: Optional[Path] = None,
) -> Path:
    """
    script: [ {"speaker": "1"|"2", "text": "..."}, ... ]
    voice_map: {"1": "male_1", "2": "female_1"}. speed: 0.5–2.0.
    on_replica_done(i, total) вызывается после каждой реплики (i — номер реплики 1..total).
    per_voice_dir: если задан, сохраняются раздельные дорожки voice_1.mp3, voice_2.mp3 (ТЗ 3.3).
    """
    segments = []  # (speaker, path) для сохранения по голосам
    default_voice = DEFAULT_VOICES[0]["id"] if DEFAULT_VOICES else "male_1"
    total = len([item for item in script if (item.get("text") or "").strip()])
    done = 0
    for item in script:
        speaker = item.get("speaker", "1")
        text = (item.get("text") or "").strip()
        if not text:
            continue
        voice_id = voice_map.get(speaker) or voice_map.get("1") or default_voice
        path = synthesize_replica(text, voice_id, speed=speed)
        segments.append((speaker, path))
        done += 1
        if on_replica_done and total:
            on_replica_done(done, total)
    if not segments:
        raise ValueError("Сценарий не содержит реплик")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    seg_paths = [p for _, p in segments]
    concatenate_audio_segments(seg_paths, output_path)
    # Раздельные дорожки по голосам (ТЗ 3.3)
    if per_voice_dir and AudioSegment is not None:
        per_speaker: Dict[str, List[Path]] = {}
        for speaker, path in segments:
            per_speaker.setdefault(speaker, []).append(path)
        per_voice_dir.mkdir(parents=True, exist_ok=True)
        for speaker, paths in per_speaker.items():
            out_voice = per_voice_dir / f"voice_{speaker}.mp3"
            concatenate_audio_segments(paths, out_voice)
    return output_path
