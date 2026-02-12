# Генератор подкастов

Веб-платформа для автоматизированного создания аудиоподкастов из текстовых материалов (PDF, DOCX, веб-статьи) с использованием AI: диалоговый сценарий, синтез речи, фоновая музыка и обложка. Готовый подкаст можно скачать в виде MP3, обложки и RSS-ленты.

---

## Содержание

- [Функциональность](#функциональность)
- [Технологии](#технологии)
- [Требования](#требования)
- [Развёртывание на локальном компьютере](#развёртывание-на-локальном-компьютере)
- [Развёртывание на виртуальном сервере](#развёртывание-на-виртуальном-сервере)
- [Переменные окружения](#переменные-окружения)
- [Структура проекта](#структура-проекта)
- [Тесты](#тесты)
- [Лицензии](#лицензии)

---

## Функциональность

Описание соответствует актуальной версии технического задания (ТЗ v1.2).

| Этап | Возможности |
|------|--------------|
| **Извлечение текста** | Парсинг PDF и DOCX (до 10 МБ), извлечение контента по URL. Автоочистка текста, удаление персональных данных (телефоны, контакты с @) с уведомлением пользователя. |
| **AI-сценарий** | Генерация диалога двух ведущих через LLM API. Настройки: формат (диалог/монолог), стиль (формальный, разговорный, энергичный), длительность, тон изложения. |
| **Синтез речи (TTS)** | Список голосов из API или fallback; превью голоса перед генерацией; настройка скорости (0.5–2.0). Итоговый трек **mixed.mp3** и **раздельные дорожки по голосам** (voice_1.mp3, voice_2.mp3) для постобработки. |
| **Музыка и обложка** | Библиотека 5–10 фоновых треков, прослушивание перед генерацией. **Автовыбор по стилю**: энергичный стиль или ускорение → melody_piano_fast.mp3, иначе → melody_piano.mp3; опция «Без мелодии» сохранена. Регулировка громкости музыки. AI-генерация обложки 1024×1024 по ключевым словам текста (поддержка proxyapi.ru и аналогов). |
| **RSS и экспорт** | Генерация RSS-ленты, MP3 с ID3-тегами, JPG-обложка. Ссылки на файлы формируются с учётом **BASE_URL** для продакшена. |
| **Интерфейс** | Главная, создание подкаста (3 шага), страница результата (плеер, скачивание MP3/обложки/RSS), страница «Подкасты» со списком выпусков. Прогресс-бар и опрос статуса раз в 1 с, кнопка отмены. |
| **Защита входа** | Один логин и пароль (по умолчанию `test` / `test`), без регистрации. Файлы для RSS (MP3, обложка, RSS) доступны по прямой ссылке **без авторизации** для подкаст-агрегаторов. |
| **Хранение и очистка** | Сроки хранения задаются в конфиге (по умолчанию 7/7/30 дней для файлов, метаданных задач и логов). **Автоочистка**: фоновый процесс раз в 24 часа и скрипт `scripts/cleanup_retention.py` для cron. |

Все внешние API (LLM, TTS, генерация изображений) подключаются через кастомный URL и API-ключ (см. `.env.example`).

---

## Технологии

- **Backend:** Python 3.10–3.12, Flask, SQLite, очередь задач (в памяти), WebSocket (Flask-SocketIO / gevent)
- **Frontend:** HTML/CSS/JS, Bootstrap 5, шаблоны Jinja2
- **Аудио:** pydub, ffmpeg
- **Внешние API:** OpenAPI-совместимые (LLM, TTS, генерация изображений) по кастомному URL

---

## Требования

- **Python 3.11** (рекомендуется) или 3.10–3.12. Для работы с аудио (pydub) нужен модуль стандартной библиотеки `audioop` — в Python 3.13 он удалён, поэтому 3.10–3.12 предпочтительны.
- **ffmpeg** — для конвертации и склейки аудио (pydub).
- **Переменные окружения** — см. [Переменные окружения](#переменные-окружения) и файл `.env.example`.

---

## Развёртывание на локальном компьютере

Подходит для разработки и тестирования (Windows или Linux/macOS).

### 1. Клонирование и окружение

```bash
git clone <url-репозитория> podcast-gen
cd podcast-gen
```

Создайте виртуальное окружение и установите зависимости:

**Windows (PowerShell):**

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

**Linux / macOS:**

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Конфигурация

Скопируйте пример конфигурации и заполните API-ключи и при необходимости пути:

```bash
cp .env.example .env
```

Отредактируйте `.env`: укажите `OPENAPI_TTS_URL`, `OPENAPI_TTS_API_KEY`; при использовании сценариев и обложек — LLM и Image API (см. комментарии в `.env.example`).

Инициализируйте БД и каталоги:

```bash
python -m scripts.init_db
```

### 3. Запуск

```bash
python -m backend.app
```

Приложение будет доступно по адресу **http://127.0.0.1:5000**. Для входа используйте логин и пароль из `.env` (по умолчанию `test` / `test`).

Остановка: в терминале `Ctrl+C`.

### Пути (Windows и Linux)

В `backend/config.py` все пути из `.env` приводятся к абсолютным; относительные пути разрешаются от **корня проекта**. В БД пути к результатам хранятся относительно `STORAGE_PATH` с прямыми слэшами — это позволяет переносить проект между Windows и Linux без изменений кода. Логи пишутся в `logs/app.log`.

---

## Развёртывание на виртуальном сервере

Рекомендуется для промышленной эксплуатации (например, VDS на Ubuntu 20.04). Подробности также в каталоге [deploy/](deploy/).

### 1. Подготовка сервера

- Установите Python 3.10+, ffmpeg и NGINX:

```bash
sudo apt update
sudo apt install -y python3.10 python3.10-venv ffmpeg nginx
```

- Создайте пользователя для приложения (не root, по ТЗ 3.7):

```bash
sudo adduser podcast
```

### 2. Развёртывание приложения

- Клонируйте репозиторий (например, в `/opt/podcast-gen`):

```bash
sudo mkdir -p /opt/podcast-gen
sudo chown podcast:podcast /opt/podcast-gen
sudo -u podcast git clone <url-репозитория> /opt/podcast-gen
```

- Виртуальное окружение и зависимости (в т.ч. Gunicorn и gevent для WebSocket):

```bash
cd /opt/podcast-gen
python3.10 -m venv venv
source venv/bin/activate
pip install -r requirements.txt gunicorn gevent-websocket
```

- Конфигурация:

```bash
cp .env.example .env
# Отредактировать .env: API-ключи, SECRET_KEY, FLASK_ENV=production, BASE_URL, LOGIN_*
python -m scripts.init_db
```

**Важно для продакшена:**

- Задайте `FLASK_ENV=production` и надёжный `SECRET_KEY`.
- Задайте **BASE_URL** — публичный URL сайта без завершающего слэша (например, `https://podcast.example.com`). Он используется в RSS и в ответах API (mp3_url, cover_url, rss_url).
- При необходимости смените `LOGIN_USERNAME` и `LOGIN_PASSWORD` от значений по умолчанию.

### 3. Автозапуск (systemd)

```bash
sudo cp deploy/podcast-gen.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable podcast-gen
sudo systemctl start podcast-gen
sudo systemctl status podcast-gen
```

### 4. NGINX

Добавьте в конфигурацию сайта фрагмент из `deploy/nginx.conf` (proxy_pass на `http://127.0.0.1:5000`). Файлы приложения (MP3, обложки, RSS) отдаются через backend; при необходимости можно настроить раздачу статики и кэширование.

### 5. Очистка по расписанию (опционально)

Для удаления старых файлов и записей по срокам из конфига можно использовать cron (в дополнение к встроенному фоновому процессу раз в 24 часа):

```bash
# Пример: ежедневно в 03:00
0 3 * * * cd /opt/podcast-gen && /opt/podcast-gen/venv/bin/python scripts/cleanup_retention.py
```

### Обновление из репозитория

```bash
cd /opt/podcast-gen
sudo -u podcast git pull origin main
sudo -u podcast /opt/podcast-gen/venv/bin/pip install -r requirements.txt
sudo systemctl restart podcast-gen
```

---

## Переменные окружения

| Переменная | Описание |
|------------|----------|
| `OPENAPI_LLM_URL`, `OPENAPI_LLM_API_KEY`, `OPENAPI_LLM_MODEL` | LLM для генерации сценария (опционально). |
| `OPENAPI_TTS_URL`, `OPENAPI_TTS_URL2`, `OPENAPI_TTS_API_KEY`, `OPENAPI_TTS_MODEL` | TTS: эндпоинт синтеза речи и ключ. |
| `OPENAPI_IMAGE_URL`, `OPENAPI_IMAGE_API_KEY`, `OPENAPI_IMAGE_MODEL` | Генерация обложки (OpenAPI-совместимый API). |
| `FLASK_ENV` | `development` или `production`. |
| `SECRET_KEY` | Секрет приложения (сессии, подпись). В продакшене — случайная строка. |
| `DATABASE_URL` | Подключение к БД (по умолчанию SQLite в `data/podcast_gen.db`). |
| **`BASE_URL`** | Публичный URL сайта без слэша в конце (для RSS и ссылок в API). Пример: `https://podcast.example.com`. |
| **`LOGIN_USERNAME`**, **`LOGIN_PASSWORD`** | Логин и пароль для входа (по умолчанию `test` / `test`). |
| `STORAGE_PATH`, `UPLOAD_PATH`, `MUSIC_LIBRARY_PATH` | Каталоги для файлов задач, загрузок и музыки (относительно корня проекта, если не задан абсолютный путь). |
| `FILE_RETENTION_DAYS`, `TASK_METADATA_DAYS`, `LOG_RETENTION_DAYS` | Сроки хранения в днях (по умолчанию 7, 7, 30). |
| `MAX_TEXT_LENGTH`, `MAX_FILE_SIZE_MB`, `TASK_TIMEOUT_SECONDS` | Лимиты текста, размера файла и таймаут задачи. |

Полный список и комментарии — в [.env.example](.env.example).

---

## Структура проекта

```
├── backend/                 # Backend приложения
│   ├── app.py               # Точка входа, регистрация маршрутов, фоновая очистка
│   ├── config.py            # Конфигурация из .env
│   ├── database.py          # SQLite, инициализация таблиц
│   ├── tasks_queue.py       # Очередь задач
│   ├── routes/              # Маршруты (main, api)
│   └── services/            # Пайплайн, TTS, музыка/обложка, RSS, очистка
├── frontend/templates/       # HTML-шаблоны (Jinja2)
├── static/                  # Музыка, сэмплы голосов
├── scripts/                 # init_db, cleanup_retention
├── deploy/                  # systemd unit, конфиг NGINX
├── tests/                   # Тесты (pytest)
├── .env.example             # Пример переменных окружения
├── requirements.txt
└── ТЗ.v1.2.md               # Техническое задание (актуальная версия)
```

---

## Тесты

```bash
# Windows
.venv\Scripts\Activate.ps1
pytest tests/ -v

# Linux / macOS
source .venv/bin/activate
pytest tests/ -v
```

---

## Лицензии

- Музыка в составе проекта — только бесплатные/открытые треки.
- Использование внешних API (TTS, LLM, генерация изображений) должно соответствовать лицензиям и правилам выбранных провайдеров.

---

**Версия ТЗ:** 1.2  
**Дата:** 2026
 
 