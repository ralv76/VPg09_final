#!/usr/bin/env python3
"""
Временный диагностический скрипт для проверки подключения к TTS API.
Перебирает варианты URL и параметров, чтобы найти рабочие:
  - список голосов (GET),
  - синтез речи / сэмпл (POST).
Запуск из корня проекта: python check_tts_connection.py
Подробный вывод в терминал.
"""
import json
import os
import sys
from pathlib import Path

# корень проекта
ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

# загрузка .env
from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

TTS_URL = (os.getenv("OPENAPI_TTS_URL") or "").strip() or None
TTS_URL2 = (os.getenv("OPENAPI_TTS_URL2") or "").strip() or None
TTS_KEY = (os.getenv("OPENAPI_TTS_API_KEY") or "").strip() or None
TTS_MODEL = (os.getenv("OPENAPI_TTS_MODEL") or "").strip() or None
VOICES_LIST_URL = (os.getenv("OPENAPI_TTS_VOICES_LIST_URL") or "").strip() or None

try:
    import httpx
except ImportError:
    print("Установите httpx: pip install httpx")
    sys.exit(1)


def log(msg: str) -> None:
    print(msg, flush=True)


def log_ok(msg: str) -> None:
    print(f"  [OK] {msg}", flush=True)


def log_fail(msg: str) -> None:
    print(f"  [FAIL] {msg}", flush=True)


def log_skip(msg: str) -> None:
    print(f"  [SKIP] {msg}", flush=True)


# --- Собираем базовые URL для перебора ---
base_urls = []
for u in (TTS_URL, TTS_URL2, VOICES_LIST_URL):
    if u:
        u = u.rstrip("/")
        if u not in base_urls:
            base_urls.append(u)

if not base_urls:
    log("В .env не заданы OPENAPI_TTS_URL, OPENAPI_TTS_URL2 или OPENAPI_TTS_VOICES_LIST_URL.")
    sys.exit(1)
if not TTS_KEY:
    log("В .env не задан OPENAPI_TTS_API_KEY.")
    sys.exit(1)

log("=" * 60)
log("ДИАГНОСТИКА TTS ПОДКЛЮЧЕНИЯ")
log("=" * 60)
log(f"Базовые URL: {base_urls}")
log(f"API key: {'*' * 8}{TTS_KEY[-4:] if len(TTS_KEY) >= 4 else '??'}")
log(f"OPENAPI_TTS_MODEL: {repr(TTS_MODEL)}")
log("")

headers = {"Authorization": f"Bearer {TTS_KEY}"}
timeout = 20.0

# --- 1) Поиск эндпоинта списка голосов ---
log("-" * 60)
log("1) ПОИСК СПИСКА ГОЛОСОВ (GET)")
log("-" * 60)

# Варианты путей для списка голосов (без дублирования v1, если base уже .../v1)
list_candidates = [
    ("/voices", "/voices"),
    ("/v1/voices", "/v1/voices"),
    ("/audio/voices", "/audio/voices"),
    ("/models", "/models"),
    ("", ""),
]

voices_found = None
working_list_url = None

for base in base_urls:
    base = base.rstrip("/")
    if voices_found is not None:
        break
    for path_short, path_full in list_candidates:
        if base.endswith("/v1") and path_full.startswith("/v1"):
            path = path_full[3:] or "/"  # /v1/voices -> /voices
        else:
            path = path_full
        url = f"{base}{path}" if path else base
        log(f"  GET {url}")
        try:
            with httpx.Client(timeout=timeout) as client:
                r = client.get(url, headers=headers)
            log(f"      Status: {r.status_code}")
            if r.status_code != 200:
                log_fail(f"{r.status_code} {r.reason_phrase}")
                if r.text:
                    log(f"      Body (first 200 chars): {r.text[:200]}")
                continue
            ct = (r.headers.get("content-type") or "").lower()
            if "json" in ct:
                data = r.json()
                # Парсим разные форматы ответа
                raw = None
                if isinstance(data, list):
                    raw = data
                elif isinstance(data, dict):
                    raw = data.get("voices") or data.get("data") or data.get("models") or data.get("voice_list")
                if raw and len(raw) > 0:
                    voices_found = []
                    for i, v in enumerate(raw):
                        if isinstance(v, dict):
                            vid = str(v.get("id", v.get("voice_id", v.get("name", i))))
                            vname = str(v.get("name", v.get("label", vid)))
                        else:
                            vid = str(v)
                            vname = vid
                        voices_found.append({"id": vid, "name": vname})
                    # Проверка: не список ли это LLM-моделей (gpt-*), а не TTS-голосов
                    looks_like_llm = any("gpt" in str(v.get("id", "")).lower() or "gpt" in str(v.get("name", "")).lower() for v in (voices_found[:5] if isinstance(raw[0], dict) else []))
                    if looks_like_llm and "/models" in url:
                        log_skip("Похоже на список моделей LLM (/models), а не голосов TTS. Запомним для подбора модели синтеза.")
                        # Сохраним id моделей с tts/audio в названии для проверки синтеза
                        tts_like = [x["id"] for x in voices_found if "tts" in x["id"].lower() or "audio" in x["id"].lower() or "speech" in x["id"].lower()]
                        if tts_like:
                            globals()["_tts_model_candidates"] = tts_like[:5]
                        voices_found = None  # не считать это списком голосов
                        continue
                    working_list_url = url
                    log_ok(f"Найдено голосов: {len(voices_found)}")
                    for v in voices_found[:10]:
                        log(f"      - id={v['id']!r} name={v['name']!r}")
                    if len(voices_found) > 10:
                        log(f"      ... и ещё {len(voices_found) - 10}")
                    break
                else:
                    log_skip("JSON без массива голосов")
                    if isinstance(data, dict):
                        log(f"      Keys: {list(data.keys())[:15]}")
            else:
                log_skip("Ответ не JSON")
        except httpx.TimeoutException:
            log_fail("Timeout")
        except Exception as e:
            log_fail(str(e))
    else:
        continue
    break

if not voices_found:
    log("")
    log("Список голосов ни по одному URL не получен. Будем пробовать синтез с тестовыми voice_id (alice, ermil, alloy, echo и т.д.).")
    # Для синтеза подставим типичные id
    voices_found = [{"id": "alice", "name": "Alice"}, {"id": "ermil", "name": "Ermil"}]
else:
    log("")
    log(f"Рабочий URL для списка голосов: {working_list_url}")
    log("")

# --- 2) Поиск эндпоинта синтеза (POST) и подбор формата тела ---
log("-" * 60)
log("2) ПОИСК СИНТЕЗА РЕЧИ (POST) И ПАРАМЕТРОВ")
log("-" * 60)

test_text = "Привет, проверка связи."
# Варианты тела запроса (разные API)
payloads = [
    {"input": test_text, "voice": "alice"},
    {"text": test_text, "voice": "alice"},
    {"input": test_text, "voice": "alice", "model": "tts-1"},
    {"input": test_text, "voice": "alice", "model": "tts-1-hd"},
    {"text": test_text, "voice_id": "alice"},
    {"text": test_text, "voice": "ermil"},
    {"input": test_text, "voice": "alloy"},
    {"input": test_text, "voice": "alloy", "model": "tts-1"},
]

# Эндпоинты для синтеза: если base уже заканчивается на /v1 — добавляем /audio/speech без дублирования v1
speech_endpoints = []
for base in base_urls:
    base = base.rstrip("/")
    # Пути относительно base (избегаем .../v1/v1/...)
    if base.endswith("/v1"):
        paths = ["", "/audio/speech", "/tts", "/synthesize"]
    else:
        paths = ["", "/v1/audio/speech", "/v1/tts", "/tts", "/synthesize", "/api/v1/audio/speech"]
    for path in paths:
        url = f"{base}{path}" if path else base
        if url not in [e[0] for e in speech_endpoints]:
            speech_endpoints.append((url, path or "(root)"))

working_speech_url = None
working_payload = None
first_voice_id = (voices_found[0]["id"] if voices_found else "alice")
tts_model_candidates = globals().get("_tts_model_candidates") or []
if TTS_MODEL:
    tts_model_candidates = [TTS_MODEL] + [m for m in tts_model_candidates if m != TTS_MODEL]

for url, path_label in speech_endpoints:
    if working_speech_url:
        break
    log(f"  POST {url}")
    # Пробуем разные форматы тела; для OpenAI-стиля нужен model
    to_try = []
    for model_val in tts_model_candidates[:5] or [None]:
        to_try.append({"input": test_text, "voice": "alloy", "model": model_val})
        to_try.append({"input": test_text, "voice": "alice", "model": model_val})
    to_try += [
        {"input": test_text, "voice": first_voice_id},
        {"text": test_text, "voice": first_voice_id},
        {"input": test_text, "voice": first_voice_id, "model": "tts-1"},
        {"input": test_text, "voice": first_voice_id, "model": "tts-1-hd"},
        {"input": test_text, "voice": first_voice_id, "model": TTS_MODEL or "tts-1"},
    ]
    if TTS_MODEL and not tts_model_candidates:
        to_try.insert(0, {"input": test_text, "voice": first_voice_id, "model": TTS_MODEL})
    for payload in to_try:
        # Убрать ключи с None (не все API принимают model=null)
        body = {k: v for k, v in payload.items() if v is not None}
        try:
            with httpx.Client(timeout=timeout) as client:
                r = client.post(url, json=body, headers={**headers, "Content-Type": "application/json"})
            log(f"      body={json.dumps(body, ensure_ascii=False)[:60]}... -> {r.status_code}")
            if r.status_code == 200:
                ct = (r.headers.get("content-type") or "").lower()
                if "audio" in ct or "octet" in ct or "mpeg" in ct:
                    working_speech_url = url
                    working_payload = body
                    log_ok(f"Синтез работает. Размер ответа: {len(r.content)} байт")
                    break
                if "application/json" in ct:
                    data = r.json()
                    # Некоторые API возвращают base64 аудио в JSON
                    b64 = (data.get("audio") or data.get("data") or data.get("content"))
                    if b64:
                        import base64
                        working_speech_url = url
                        working_payload = body
                        log_ok(f"Синтез работает (JSON+base64). Размер: {len(base64.b64decode(b64))} байт")
                        break
                    log_skip("JSON без поля audio/data")
                else:
                    # Сырые байты без content-type — возможно аудио
                    if len(r.content) > 100 and not (r.text and r.text.startswith("{")):
                        working_speech_url = url
                        working_payload = body
                        log_ok(f"Синтез работает (бинарный ответ). Размер: {len(r.content)} байт")
                        break
            else:
                if r.text:
                    log_fail(f"Response: {r.text[:150]}")
        except httpx.TimeoutException:
            log_fail("Timeout")
        except Exception as e:
            log_fail(str(e))
    if not working_speech_url:
        log("")
    else:
        break

# --- Итог и рекомендации для .env ---
log("")
log("=" * 60)
log("ИТОГ")
log("=" * 60)

if working_list_url:
    log_ok(f"Список голосов: {working_list_url}")
    log(f"  -> В .env задайте OPENAPI_TTS_VOICES_LIST_URL={working_list_url}")
else:
    log_fail("Список голосов: ни один URL не подошёл")
    if working_speech_url:
        log("  (Для OpenAI-стиля TTS голоса часто фиксированы: alloy, echo, fable, onyx, nova, shimmer. Приложение может использовать их по умолчанию.)")

if working_speech_url and working_payload:
    log_ok(f"Синтез речи: POST {working_speech_url}")
    log(f"  Тело запроса (образец): {json.dumps(working_payload, ensure_ascii=False)}")
    log(f"  -> В .env задайте OPENAPI_TTS_URL={working_speech_url}")
    if working_payload.get("model"):
        log(f"  -> Задайте OPENAPI_TTS_MODEL={working_payload['model']!r} (если нужны сэмплы с учётом модели)")
else:
    log_fail("Синтез речи: ни один вариант POST не вернул аудио")

log("")
log("После подбора значений скопируйте их в .env и перезапустите приложение.")
log("")
