"""Flask application factory. ТЗ 4.2: REST API, modular structure."""
import logging
import os
import threading
import time
from pathlib import Path

from flask import Flask

from backend.config import STORAGE_PATH, UPLOAD_PATH, MUSIC_LIBRARY_PATH, DATA_DIR, BASE_DIR

# Расширенное логирование: в терминал и в один файл logs/app.log
LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "app.log"
log_fmt = "%(asctime)s %(levelname)s [%(name)s] %(message)s"
logging.basicConfig(level=logging.INFO, format=log_fmt)
logging.getLogger("werkzeug").setLevel(logging.WARNING)
try:
    fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
    fh.setFormatter(logging.Formatter(log_fmt))
    fh.setLevel(logging.DEBUG)
    logging.getLogger().addHandler(fh)
except Exception:
    pass

from backend.database import init_db


def create_app():
    app = Flask(
        __name__,
        static_folder=str(BASE_DIR / "frontend" / "static"),
        template_folder=str(BASE_DIR / "frontend" / "templates"),
    )
    app.config.from_object("backend.config")

    for d in (DATA_DIR, STORAGE_PATH, UPLOAD_PATH, MUSIC_LIBRARY_PATH):
        d.mkdir(parents=True, exist_ok=True)

    init_db()

    from backend.routes import register_routes
    register_routes(app)
    from backend.routes.ws import sock
    sock.init_app(app)

    # Защита входа (ТЗ 2.2.7): один логин/пароль; файлы для RSS доступны без пароля
    from flask import request, redirect, url_for, session

    @app.before_request
    def require_login():
        if session.get("logged_in"):
            return None
        path = request.path
        if path in ("/login", "/logout"):
            return None
        if path == "/api/health":
            return None
        # Публичные URL для RSS: MP3, обложка, RSS-файл по прямой ссылке
        if path.startswith("/api/files/") and (path.endswith("/mp3") or path.endswith("/cover") or path.endswith("/rss")):
            return None
        return redirect(url_for("main.login", next=request.url))

    # Фоновая очистка по срокам хранения (ТЗ 5.2): раз в 24 часа
    def _cleanup_loop():
        interval = 24 * 60 * 60  # секунд
        while True:
            time.sleep(interval)
            try:
                from backend.services.cleanup import run_retention_cleanup
                run_retention_cleanup()
            except Exception as e:
                logging.getLogger(__name__).exception("cleanup_loop: %s", e)

    t = threading.Thread(target=_cleanup_loop, daemon=True)
    t.start()

    return app


app = create_app()

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=app.config.get("DEBUG", False))
