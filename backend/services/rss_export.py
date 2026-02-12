"""RSS 2.0 и экспорт. ТЗ 3.6, 5.1."""
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional
from xml.etree import ElementTree as ET

from mutagen.mp3 import MP3
from mutagen.id3 import ID3, TIT2, TPE1, APIC

from backend.config import STORAGE_PATH

logger = logging.getLogger(__name__)


def build_rss(
    title: str,
    description: str,
    mp3_url: str,
    cover_url: str,
    duration_seconds: int,
    pub_date: datetime,
    rss_url: str,
) -> str:
    """Формирование RSS 2.0 для подкаст-платформ. ТЗ 3.6."""
    rfc_date = pub_date.strftime("%a, %d %b %Y %H:%M:%S +0000")
    channel = ET.Element("rss", version="2.0", attrib={"xmlns:itunes": "http://www.itunes.com/dtds/podcast-1.0.dtd"})
    ch = ET.SubElement(channel, "channel")
    ET.SubElement(ch, "title").text = title or "Подкаст"
    ET.SubElement(ch, "description").text = description or "Сгенерированный подкаст"
    ET.SubElement(ch, "link").text = rss_url
    ET.SubElement(ch, "lastBuildDate").text = rfc_date
    ET.SubElement(ch, "language").text = "ru"
    img = ET.SubElement(ch, "image")
    ET.SubElement(img, "url").text = cover_url
    ET.SubElement(img, "title").text = title or "Подкаст"
    item = ET.SubElement(ch, "item")
    ET.SubElement(item, "title").text = title or "Выпуск"
    ET.SubElement(item, "description").text = description or ""
    ET.SubElement(item, "pubDate").text = rfc_date
    ET.SubElement(item, "enclosure", attrib={"url": mp3_url, "type": "audio/mpeg", "length": str(duration_seconds * 16000)})
    ET.SubElement(item, "guid", attrib={"isPermaLink": "false"}).text = mp3_url
    tree = ET.ElementTree(channel)
    ET.indent(tree, space="  ")
    return ET.tostring(channel, encoding="unicode", method="xml")


def write_id3(mp3_path: Path, title: str, cover_path: Optional[Path] = None) -> None:
    """Запись ID3: название, обложка. ТЗ 3.6. Используем audio.tags (ID3), не audio.add()."""
    try:
        audio = MP3(str(mp3_path), ID3=ID3)
    except Exception:
        audio = MP3(str(mp3_path))
    try:
        if audio.tags is None:
            audio.add_tags(ID3())
        audio.tags.add(TIT2(encoding=3, text=title or "Подкаст"))
        audio.tags.add(TPE1(encoding=3, text="Генератор подкастов"))
        audio.save()
    except Exception as e:
        logger.warning("ID3 write: %s", e)
    if cover_path and cover_path.exists():
        try:
            if audio.tags is None:
                audio.add_tags(ID3())
            with open(cover_path, "rb") as f:
                audio.tags.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="Cover", data=f.read()))
            audio.save()
        except Exception as e:
            logger.warning("ID3 cover: %s", e)


def get_mp3_duration_seconds(mp3_path: Path) -> int:
    try:
        return int(MP3(str(mp3_path)).info.length)
    except Exception:
        return 0
