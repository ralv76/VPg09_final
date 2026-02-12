"""Application configuration from environment.
Пути нормализованы для работы и на Windows (среда разработки), и на Linux (сервер):
- относительные пути из .env разрешаются относительно BASE_DIR (корень проекта);
- все пути приводятся к абсолютным через resolve();
- в БД сохраняем пути относительно STORAGE_PATH для переносимости.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent


def _resolve_path(env_value: str, default: Path) -> Path:
    """Абсолютный путь: если env_value относительный — разрешаем от BASE_DIR (одинаково на Win и Linux)."""
    p = Path(env_value) if env_value else default
    if not p.is_absolute():
        p = BASE_DIR / p
    return p.resolve()


# Paths (везде абсолютные, относительные в .env — от корня проекта)
_storage = os.getenv("STORAGE_PATH", "").strip() or str(BASE_DIR / "storage")
_upload = os.getenv("UPLOAD_PATH", "").strip() or str(BASE_DIR / "uploads")
_music = os.getenv("MUSIC_LIBRARY_PATH", "").strip() or str(BASE_DIR / "static" / "music")
_voice_samples = os.getenv("VOICE_SAMPLES_DIR", "").strip() or str(BASE_DIR / "static" / "voice_samples")

STORAGE_PATH = _resolve_path(_storage, BASE_DIR / "storage")
UPLOAD_PATH = _resolve_path(_upload, BASE_DIR / "uploads")
MUSIC_LIBRARY_PATH = _resolve_path(_music, BASE_DIR / "static" / "music")
VOICE_SAMPLES_DIR = _resolve_path(_voice_samples, BASE_DIR / "static" / "voice_samples")
DATA_DIR = (BASE_DIR / "data").resolve()


def resolve_storage_path(stored: str) -> Path:
    """Путь к файлу из БД. Win/Linux: относительный от STORAGE_PATH (переносимый) или абсолютный если файл есть."""
    if not (stored and stored.strip()):
        return STORAGE_PATH / "_none_"
    normalized = stored.replace("\\", "/").strip()
    p = Path(normalized)
    if p.is_absolute() and p.exists():
        return p.resolve()
    # Относительный путь или старый абсолютный с другой ОС — собираем от STORAGE_PATH
    rel = normalized
    if "storage/" in normalized:
        parts = normalized.split("storage/")
        if len(parts) > 1:
            rel = parts[-1]
    elif "storage\\" in normalized:
        parts = normalized.replace("\\", "/").split("storage/")
        if len(parts) > 1:
            rel = parts[-1]
    return (STORAGE_PATH / rel).resolve()


# App
SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-change-in-production")
FLASK_ENV = os.getenv("FLASK_ENV", "development")
DEBUG = FLASK_ENV == "development"

# Защита входа: один логин/пароль без регистрации (ТЗ 2.2.7). Файлы для RSS доступны без пароля.
LOGIN_USERNAME = os.getenv("LOGIN_USERNAME", "test")
LOGIN_PASSWORD = os.getenv("LOGIN_PASSWORD", "test")

# Database (URI с прямыми слэшами для работы на Win и Linux)
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{(DATA_DIR / 'podcast_gen.db').as_posix()}")

# Limits (ТЗ 2.3: budget, 10 MB files)
MAX_TEXT_LENGTH = int(os.getenv("MAX_TEXT_LENGTH", "50000"))
MAX_FILE_SIZE_MB = int(os.getenv("MAX_FILE_SIZE_MB", "10"))
MAX_FILE_SIZE_BYTES = MAX_FILE_SIZE_MB * 1024 * 1024
TASK_TIMEOUT_SECONDS = int(os.getenv("TASK_TIMEOUT_SECONDS", "600"))

# Retention (ТЗ 5.2)
FILE_RETENTION_DAYS = int(os.getenv("FILE_RETENTION_DAYS", "7"))
TASK_METADATA_DAYS = int(os.getenv("TASK_METADATA_DAYS", "7"))
LOG_RETENTION_DAYS = int(os.getenv("LOG_RETENTION_DAYS", "30"))

# Публичный URL для RSS и ссылок на файлы (продакшен). Без завершающего слэша.
BASE_URL = (os.getenv("BASE_URL") or os.getenv("PUBLIC_URL") or "").strip() or None

# OpenAPI (custom URL + API_KEY)
OPENAPI_LLM_URL = os.getenv("OPENAPI_LLM_URL", "").strip() or None
OPENAPI_LLM_API_KEY = os.getenv("OPENAPI_LLM_API_KEY", "").strip() or None
OPENAPI_LLM_MODEL = os.getenv("OPENAPI_LLM_MODEL", "").strip() or None

OPENAPI_TTS_URL = os.getenv("OPENAPI_TTS_URL", "").strip() or None
OPENAPI_TTS_URL2 = os.getenv("OPENAPI_TTS_URL2", "").strip() or None  # запасной URL при ошибке по первому
OPENAPI_TTS_API_KEY = os.getenv("OPENAPI_TTS_API_KEY", "").strip() or None
OPENAPI_TTS_VOICES = os.getenv("OPENAPI_TTS_VOICES", "").strip() or None
# Модель TTS (для различения сэмплов: имя файла включает модель)
OPENAPI_TTS_MODEL = os.getenv("OPENAPI_TTS_MODEL", "").strip() or None
# URL для получения списка голосов (если API поддерживает), иначе используется fallback-список
OPENAPI_TTS_VOICES_LIST_URL = os.getenv("OPENAPI_TTS_VOICES_LIST_URL", "").strip() or None

OPENAPI_IMAGE_URL = os.getenv("OPENAPI_IMAGE_URL", "").strip() or None
OPENAPI_IMAGE_API_KEY = os.getenv("OPENAPI_IMAGE_API_KEY", "").strip() or None
OPENAPI_IMAGE_MODEL = os.getenv("OPENAPI_IMAGE_MODEL", "").strip() or None
# Качество изображения (для gpt-image-1.5 и аналогов: low, medium, high)
OPENAPI_IMAGE_QUALITY = os.getenv("OPENAPI_IMAGE_QUALITY", "").strip() or None
