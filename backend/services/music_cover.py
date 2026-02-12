"""Музыкальная библиотека и микширование; генерация обложки. ТЗ 2.1.5, 3.5."""
import logging
import random
from pathlib import Path
from typing import Optional

import httpx

try:
    from pydub import AudioSegment
except ImportError:
    AudioSegment = None  # Python 3.13+ без pyaudioop: установите pyaudioop или используйте Python 3.12

from backend.config import (
    MUSIC_LIBRARY_PATH,
    OPENAPI_IMAGE_URL,
    OPENAPI_IMAGE_API_KEY,
    OPENAPI_IMAGE_MODEL,
    OPENAPI_IMAGE_QUALITY,
    STORAGE_PATH,
)

logger = logging.getLogger(__name__)

COVER_SIZE = 1024
MUSIC_VOLUME_DB = -20  # громкость музыки относительно голоса (ТЗ 7.1 — регулируемая)


def list_music_tracks() -> list:
    """Список треков в библиотеке (5–10). ТЗ 2.1.5."""
    if not MUSIC_LIBRARY_PATH.exists():
        return []
    exts = {".mp3", ".wav", ".m4a"}
    tracks = []
    for p in MUSIC_LIBRARY_PATH.iterdir():
        if p.suffix.lower() in exts and p.is_file():
            tracks.append({"id": p.stem, "name": p.name, "path": str(p.resolve())})
    return tracks[:10]


def pick_music_for_text(text: str) -> Optional[Path]:
    """Выбор трека по теме/ключевым словам или случайно. MVP: случайный."""
    tracks = list_music_tracks()
    if not tracks:
        return None
    return Path(random.choice(tracks)["path"])


def pick_music_by_style(style: str, voice_speed: float) -> Optional[str]:
    """
    Подбор музыки по стилю и скорости воспроизведения (ТЗ 3.5).
    Энергичный стиль или ускорение (> 1.0) → melody_piano_fast.mp3, иначе → melody_piano.mp3.
    Возвращает id трека или None (вариант без музыки не удаляется — вызывающий код решает).
    """
    tracks = list_music_tracks()
    ids = {t["id"] for t in tracks}
    energetic = (style or "").strip().lower() in ("energetic", "энергичный")
    speed_up = float(voice_speed) > 1.0
    if energetic or speed_up:
        return "melody_piano_fast" if "melody_piano_fast" in ids else (list(ids)[0] if ids else None)
    return "melody_piano" if "melody_piano" in ids else (list(ids)[0] if ids else None)


def mix_voice_with_music(voice_path: Path, music_path: Optional[Path], output_path: Path, music_volume_db: float = MUSIC_VOLUME_DB) -> Path:
    """Микширование: голос + музыка. Громкость музыки регулируемая. ТЗ 2.1.5, 7.1."""
    if AudioSegment is None:
        raise RuntimeError("pydub недоступен (нужен audioop). Используйте Python 3.12 или установите pyaudioop.")
    voice = AudioSegment.from_file(str(voice_path))
    if not music_path or not music_path.exists():
        voice.export(str(output_path), format="mp3", bitrate="128k")
        return output_path
    music = AudioSegment.from_file(str(music_path))
    if len(music) < len(voice):
        music = music * (len(voice) // len(music) + 1)
    music = music[:len(voice)]
    music = music + music_volume_db
    mixed = voice.overlay(music)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    mixed.export(str(output_path), format="mp3", bitrate="128k")
    return output_path


# Суффикс промпта: без текста на изображении (модель плохо рисует кириллицу; достаточно визуала).
_COVER_NO_TEXT_SUFFIX = ", no text, no letters, no words, no typography, visual only, illustration and icons only"

def generate_cover_prompt(text: str) -> str:
    """
    Промпт для обложки только на английском, без текста на изображении (ТЗ 3.5).
    Визуал, иконки и графику сохраняем; русский язык в промпте не используем.
    """
    # Тематику передаём общим описанием на английском, без русских слов в промпте
    prompt = "Professional podcast cover art, abstract modern illustration, subtle graphic elements and icons, clean design"
    return prompt + _COVER_NO_TEXT_SUFFIX


def _has_cyrillic(s: str) -> bool:
    return any("\u0400" <= c <= "\u04FF" for c in (s or ""))


def generate_cover_image(prompt: str, size: int = COVER_SIZE, custom_prompt: Optional[str] = None) -> bytes:
    """
    Генерация обложки через OpenAPI-совместимый API (кастомный URL + API_KEY).
    Размер 1024×1024. ТЗ 3.5. Промпт только на английском, без текста на изображении.
    Для proxyapi.ru и аналогов: модель не передаётся по умолчанию или используйте dall-e-3 / dall-e-2.
    """
    if not OPENAPI_IMAGE_URL or not OPENAPI_IMAGE_API_KEY:
        raise RuntimeError("Генерация изображений не настроена: OPENAPI_IMAGE_URL, OPENAPI_IMAGE_API_KEY")
    raw = (custom_prompt or prompt or "podcast cover art").strip()
    # Не передаём в API промпты с кириллицей — избегаем текста на русском на картинке
    if _has_cyrillic(raw):
        raw = prompt.strip() if prompt else "Professional podcast cover art, abstract illustration, no text"
    text_prompt = (raw + _COVER_NO_TEXT_SUFFIX if _COVER_NO_TEXT_SUFFIX not in raw else raw)[:1000]
    url = OPENAPI_IMAGE_URL.rstrip("/")
    # OpenAI-стиль: эндпоинт картинок — /v1/images/generations (если base заканчивается на /v1)
    if url.endswith("/v1"):
        url = url + "/images/generations"
    headers = {"Authorization": f"Bearer {OPENAPI_IMAGE_API_KEY}"}
    # Список моделей, поддерживаемых многими прокси (proxyapi.ru, proxed и т.д.)
    _IMAGE_MODELS_SAFE = ("dall-e-3", "dall-e-2", "gpt-image-1", "gpt-image-1.5")
    use_model = OPENAPI_IMAGE_MODEL if OPENAPI_IMAGE_MODEL else None
    if use_model and use_model.lower() not in _IMAGE_MODELS_SAFE:
        logger.info("[music_cover] Модель %s может не поддерживаться API; при 400 будет повтор без model", use_model)
    # Некоторые API (proxyapi.ru и др.) не принимают response_format — при 400 повторим без него
    payload = {"prompt": text_prompt, "size": f"{size}x{size}", "n": 1, "response_format": "b64_json"}
    if use_model:
        payload["model"] = use_model
    # Для gpt-image-1.5 и аналогов — качество low по умолчанию (можно задать OPENAPI_IMAGE_QUALITY в .env)
    quality = OPENAPI_IMAGE_QUALITY or (("gpt-image-1.5" in (use_model or "").lower()) and "low" or None)
    if quality:
        payload["quality"] = quality.lower()
    with httpx.Client(timeout=120.0) as client:
        resp = client.post(url, json=payload, headers=headers)
        if not resp.is_success:
            body = (resp.text or "")[:500]
            logger.warning("[music_cover] API изображений ответил %s: %s", resp.status_code, body)
            # Повтор без model при "Model not supported"
            if resp.status_code == 400 and "model" in body.lower() and "model" in payload:
                logger.info("[music_cover] Повтор запроса без параметра model")
                del payload["model"]
                resp = client.post(url, json=payload, headers=headers)
                if not resp.is_success:
                    body = (resp.text or "")[:500]
                    logger.warning("[music_cover] Повтор без model: %s %s", resp.status_code, body)
            # Повтор без response_format при "Unknown parameter: response_format" (proxyapi.ru)
            if not resp.is_success:
                body = (resp.text or "")[:500]
            if not resp.is_success and resp.status_code == 400 and "response_format" in body.lower() and "response_format" in payload:
                logger.info("[music_cover] Повтор запроса без параметра response_format")
                del payload["response_format"]
                resp = client.post(url, json=payload, headers=headers)
                if not resp.is_success:
                    logger.warning("[music_cover] Повтор без response_format: %s %s", resp.status_code, (resp.text or "")[:300])
        resp.raise_for_status()
        ct = (resp.headers.get("content-type") or "").lower()
        if "application/json" in ct:
            data = resp.json()
            import base64
            b64 = data.get("data", [{}])[0].get("b64_json") or data.get("b64_json") or data.get("image")
            if b64:
                return base64.b64decode(b64)
            url_out = data.get("data", [{}])[0].get("url") or data.get("url")
            if url_out:
                r2 = client.get(url_out)
                r2.raise_for_status()
                return r2.content
            raise ValueError("Ответ API изображений без data/url")
        return resp.content
