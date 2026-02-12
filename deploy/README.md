# Деплой на VDS RUSONIX (Ubuntu 20.04)

ТЗ 3.7: пользователь не root; доступ к файлам подкастов только через backend.

## Подготовка сервера

1. Создать пользователя (если не root-доступ):
   ```bash
   sudo adduser podcast
   sudo usermod -aG podcast podcast
   ```

2. Установить Python 3.10+, ffmpeg, NGINX:
   ```bash
   sudo apt update
   sudo apt install -y python3.10 python3.10-venv ffmpeg nginx
   ```

## Развёртывание приложения

1. Клонировать репозиторий (или загрузить код) в `/opt/podcast-gen` (владелец `podcast`):
   ```bash
   sudo mkdir -p /opt/podcast-gen
   sudo chown podcast:podcast /opt/podcast-gen
   sudo -u podcast git clone <repo> /opt/podcast-gen
   ```

2. Виртуальное окружение и зависимости:
   ```bash
   cd /opt/podcast-gen
   python3.10 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt gunicorn gevent-websocket
   ```

3. Конфигурация:
   ```bash
   cp .env.example .env
   # Отредактировать .env: API ключи, SECRET_KEY, пути
   python -m scripts.init_db
   ```

4. systemd (автозапуск при перезагрузке, ТЗ 7.2):
   ```bash
   sudo cp deploy/podcast-gen.service /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable podcast-gen
   sudo systemctl start podcast-gen
   sudo systemctl status podcast-gen
   ```

5. NGINX: добавить в свой `server` блок фрагмент из `deploy/nginx.conf` (proxy_pass на 127.0.0.1:5000).

## Переменные окружения

См. `.env.example`. Обязательно задать на сервере:

- `FLASK_ENV=production`
- `SECRET_KEY` — случайная строка
- `OPENAPI_TTS_URL`, `OPENAPI_TTS_API_KEY` (и при необходимости LLM, Image)

## Деплой из main (обновление)

```bash
cd /opt/podcast-gen
sudo -u podcast git pull origin main
sudo -u podcast /opt/podcast-gen/venv/bin/pip install -r requirements.txt
sudo systemctl restart podcast-gen
```
