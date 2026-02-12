"""Извлечение текста из PDF, DOCX и URL. Без внешних API. ТЗ 2.1.1."""
import re
import logging
from pathlib import Path
from typing import Optional

import fitz  # PyMuPDF
from docx import Document as DocxDocument
import requests
from bs4 import BeautifulSoup

from backend.config import MAX_FILE_SIZE_BYTES

logger = logging.getLogger(__name__)

# Лимит 10 МБ
MAX_SIZE = MAX_FILE_SIZE_BYTES

# ПД: телефоны — разные варианты написания (ТЗ: маска под самые разные варианты)
PHONE_PATTERNS = [
    re.compile(r"\+7\s*\(?\d{3}\)?\s*\d{3}[\s\-]?\d{2}[\s\-]?\d{2}"),  # +7 (999) 123-45-67
    re.compile(r"8\s*\(?\d{3}\)?\s*\d{3}[\s\-]?\d{2}[\s\-]?\d{2}"),   # 8 (999) 123-45-67
    re.compile(r"\+?\d{1,3}[\s\-\.]?\(?\d{2,4}\)?[\s\-\.]?\d{2,4}[\s\-\.]?\d{2,4}[\s\-\.]?\d{2,4}"),  # международные
    re.compile(r"\d{3}[\s\-]?\d{2}[\s\-]?\d{2}"),  # 123-45-67
    re.compile(r"\(\d{3}\)\s*\d{3}[\s\-]?\d{2}[\s\-]?\d{2}"),
]
# Всё, что содержит @ — считаем контактом (email, Telegram и т.д.): удаляем слово слева и справа от @
# Паттерн: непробельные символы слева от @, потом @, потом непробельные справа
EMAIL_OR_CONTACT_PATTERN = re.compile(r"\S+@\S+")


def mask_pii(text: str):
    """
    Удаление/маскирование телефонов и контактов с @ (email, TG и т.д.).
    Возвращает (очищенный_текст, список_удалённых_телефонов, список_удалённых_контактов_с_@).
    """
    removed_phones = []
    removed_contacts = []
    out = text
    for pat in PHONE_PATTERNS:
        for m in pat.finditer(out):
            removed_phones.append(m.group(0))
        out = pat.sub("[телефон скрыт]", out)
    for m in EMAIL_OR_CONTACT_PATTERN.finditer(out):
        removed_contacts.append(m.group(0))
    out = EMAIL_OR_CONTACT_PATTERN.sub("[контакт скрыт]", out)
    return out, removed_phones, removed_contacts


def mask_pii_legacy(text: str) -> str:
    """Обратная совместимость: только текст."""
    t, _, _ = mask_pii(text)
    return t


def _has_visible_chars(s: str) -> bool:
    """Есть ли в строке хотя бы один видимый (не пробел/управляющий) символ."""
    return any(c.isprintable() and not c.isspace() for c in (s or ""))


def clean_and_format(text: str) -> str:
    """Убрать пустые и строки без видимых символов, схлопнуть переносы, один абзац — одна строка."""
    if not text or not text.strip():
        return ""
    text = re.sub(r"\n{3,}", "\n\n", text)
    lines = []
    for line in text.splitlines():
        s = line.strip()
        if s and _has_visible_chars(s):
            lines.append(s)
    return "\n\n".join(lines)


def _extract_raw_pdf(file_path: Path) -> str:
    if file_path.stat().st_size > MAX_SIZE:
        raise ValueError(f"Файл превышает лимит {MAX_SIZE // (1024*1024)} МБ")
    text_parts = []
    doc = fitz.open(file_path)
    try:
        for page in doc:
            text_parts.append(page.get_text())
    finally:
        doc.close()
    return "\n".join(text_parts)


def _extract_raw_docx(file_path: Path) -> str:
    if file_path.stat().st_size > MAX_SIZE:
        raise ValueError(f"Файл превышает лимит {MAX_SIZE // (1024*1024)} МБ")
    doc = DocxDocument(file_path)
    parts = [p.text for p in doc.paragraphs if p.text.strip()]
    return "\n\n".join(parts)


def extract_from_pdf(file_path: Path):
    """Извлечение текста из PDF. Возвращает (текст, удалённые_телефоны, удалённые_контакты_с_@)."""
    raw = _extract_raw_pdf(file_path)
    raw, phones, contacts = mask_pii(raw)
    return clean_and_format(raw), phones, contacts


def extract_from_docx(file_path: Path):
    """Извлечение текста из DOCX. Возвращает (текст, удалённые_телефоны, удалённые_контакты_с_@)."""
    raw = _extract_raw_docx(file_path)
    raw, phones, contacts = mask_pii(raw)
    return clean_and_format(raw), phones, contacts


def extract_from_url(url: str):
    """Извлечение текста с веб-страницы. Возвращает (текст, удалённые_телефоны, удалённые_контакты_с_@)."""
    headers = {"User-Agent": "PodcastGenerator/1.0 (educational project)"}
    resp = requests.get(url, timeout=30, headers=headers)
    resp.raise_for_status()
    if len(resp.content) > MAX_SIZE:
        raise ValueError("Размер страницы превышает допустимый лимит")
    soup = BeautifulSoup(resp.text, "lxml")
    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()
    body = soup.find("body") or soup
    if not body:
        raise ValueError("Не удалось извлечь контент страницы")
    raw = body.get_text(separator="\n", strip=True)
    raw, phones, contacts = mask_pii(raw)
    return clean_and_format(raw), phones, contacts


def extract_text(source: str, file_path: Optional[Path] = None):
    """Единая точка входа. Возвращает (текст, phones, contacts)."""
    if file_path is not None:
        suf = file_path.suffix.lower()
        if suf == ".pdf":
            return extract_from_pdf(file_path)
        if suf in (".docx", ".doc"):
            return extract_from_docx(file_path)
        raise ValueError(f"Неподдерживаемый формат файла: {suf}. Поддерживаются PDF и DOCX.")
    raise ValueError("Укажите file_path или используйте extract_from_url для URL.")
