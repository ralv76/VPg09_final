"""Универсальный клиент к OpenAPI-совместимому LLM. ТЗ 4.2: кастомный URL + API_KEY."""
import logging
import re
from typing import List, Dict, Any, Optional

from openai import OpenAI
from openai import APITimeoutError, APIError

from backend.config import OPENAPI_LLM_URL, OPENAPI_LLM_API_KEY, OPENAPI_LLM_MODEL

logger = logging.getLogger(__name__)

# Форматы реплик для парсинга: "Ведущий 1: текст" или "А: текст", "Speaker 1: text"
REPLICA_PATTERN = re.compile(
    r"^(?:Ведущий\s*[12]|Участник\s*[12]|А|Б|Speaker\s*[12]|Host\s*[12])\s*[:\-]\s*(.+)$",
    re.IGNORECASE | re.MULTILINE
)
ALT_PATTERN = re.compile(r"^([АБA-B12])\s*[\.\:\-]\s*(.+)$", re.MULTILINE)


def get_client() -> Optional[OpenAI]:
    if not OPENAPI_LLM_URL or not OPENAPI_LLM_API_KEY:
        return None
    base = OPENAPI_LLM_URL.rstrip("/")
    if not base.endswith("/v1"):
        base = base + "/v1"
    return OpenAI(base_url=base, api_key=OPENAPI_LLM_API_KEY)


STYLE_MAP = {
    "formal": "формальный",
    "conversational": "разговорный",
    "energetic": "энергичный",
    "формальный": "формальный",
    "разговорный": "разговорный",
    "энергичный": "энергичный",
}

DURATION_MAP = {
    "very_short": "до 1 минуты",
    "short": "3-5 минут",
    "standard": "7-10 минут",
    "краткий": "3-5 минут",
    "стандартный": "7-10 минут",
    "микро": "до 1 минуты",
}

# Подача/угол подкаста: как преподносить контент
PRESENTATION_MAP = {
    "company_reminder": "как напоминание от компании: получили интересное напоминание, напоминают вопросы, которые нужно решить, с ненавязчивой рекламой или упоминанием услуг",
    "knowledge_broadcast": "как трансляция знаний: «знаете ли вы, что…», подача основных фактов и идей из материала голосом ведущих",
    "educational": "как обучение: «обращаем внимание, чтобы сделать то-то — нужно то-то», пошаговые пояснения и акценты",
    "neutral": "нейтральная подача: просто изложение материала в формате подкаста без специального угла",
    "storytelling": "как история: подача в виде повествования с завязкой и выводом",
}


def build_prompt(
    text: str,
    format_type: str = "dialog",
    style: str = "conversational",
    duration: str = "standard",
    presentation: str = "neutral",
) -> str:
    """Промпт для превращения текста в диалог. presentation задаёт угол подачи (company_reminder, knowledge_broadcast, educational и т.д.)."""
    style_ru = STYLE_MAP.get(style.lower(), style)
    duration_ru = DURATION_MAP.get(duration.lower(), duration)
    presentation_ru = PRESENTATION_MAP.get((presentation or "neutral").lower(), presentation or "нейтральная подача")
    if format_type.lower() == "monologue" or format_type == "монолог":
        return (
            f"Переработай следующий текст в сценарий короткого подкаста-монолога. "
            f"Подача: {presentation_ru}. Стиль: {style_ru}. Длительность: {duration_ru}. "
            f"Сохрани ключевые идеи. Выдай только текст сценария, без пояснений.\n\n{text[:15000]}"
        )
    return (
        f"Переработай следующий текст в сценарий подкаста — диалог двух ведущих (Ведущий 1 и Ведущий 2). "
        f"Подача: {presentation_ru}. Стиль: {style_ru}. Длительность: {duration_ru}. "
        f"Естественные реплики, вопросы и переходы между темами. Сохрани ключевые идеи. "
        f"Формат ответа: каждая реплика с новой строки, начинается с «Ведущий 1:» или «Ведущий 2:». "
        f"Выдай только сценарий, без вступления.\n\n{text[:15000]}"
    )


def parse_scenario_response(raw: str) -> List[Dict[str, str]]:
    """Парсинг ответа LLM: список реплик с меткой говорящего."""
    lines = raw.strip().split("\n")
    result = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        m = REPLICA_PATTERN.match(line)
        if m:
            replica_text = m.group(1).strip()
            if len(result) and result[-1].get("speaker") == "2":
                speaker = "1"
            elif len(result) and result[-1].get("speaker") == "1":
                speaker = "2"
            else:
                speaker = "1" if "1" in line[:20] or "Ведущий 1" in line or "А:" in line[:5] else "2"
            result.append({"speaker": speaker, "text": replica_text})
            continue
        m = ALT_PATTERN.match(line)
        if m:
            result.append({"speaker": "1" if m.group(1) in "АA1" else "2", "text": m.group(2).strip()})
            continue
        if result:
            result[-1]["text"] += " " + line
    if not result and raw.strip():
        result.append({"speaker": "1", "text": raw.strip()[:5000]})
    return result


def generate_script(
    text: str,
    format_type: str = "dialog",
    style: str = "conversational",
    duration: str = "standard",
    presentation: str = "neutral",
) -> List[Dict[str, str]]:
    """
    Генерация сценария через LLM. Retry при таймауте/ошибке. ТЗ 8.1.
    Возвращает список {"speaker": "1"|"2", "text": "..."}.
    """
    client = get_client()
    if not client:
        logger.warning("LLM not configured: OPENAPI_LLM_URL and OPENAPI_LLM_API_KEY required")
        return [{"speaker": "1", "text": text[:3000]}]  # fallback: one block
    model = OPENAPI_LLM_MODEL or "gpt-3.5-turbo"
    prompt = build_prompt(text, format_type, style, duration, presentation)
    last_error = None
    for attempt in range(3):
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                timeout=120.0,
            )
            content = (resp.choices[0].message.content or "").strip()
            return parse_scenario_response(content)
        except APITimeoutError as e:
            last_error = e
            logger.warning("LLM timeout attempt %s: %s", attempt + 1, e)
        except APIError as e:
            last_error = e
            logger.warning("LLM API error attempt %s: %s", attempt + 1, e)
    raise last_error or RuntimeError("LLM failed")
