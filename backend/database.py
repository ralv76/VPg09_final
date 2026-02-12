"""SQLite database and models. ТЗ 5.1: Session, Task, Result."""
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from backend.config import DATABASE_URL, DATA_DIR


def _get_db_path():
    url = DATABASE_URL
    if url.startswith("sqlite:///"):
        path = url.replace("sqlite:///", "")
        return Path(path)
    return DATA_DIR / "podcast_gen.db"


@contextmanager
def get_connection():
    path = _get_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    """Create tables: session, task, result."""
    path = _get_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    with get_connection() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS session (
                id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS task (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                stage TEXT,
                error_message TEXT,
                result_id TEXT,
                params_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (session_id) REFERENCES session(id)
            );

            CREATE TABLE IF NOT EXISTS result (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL UNIQUE,
                mp3_path TEXT,
                cover_path TEXT,
                rss_path TEXT,
                title TEXT,
                description TEXT,
                duration_seconds INTEGER,
                created_at TEXT NOT NULL,
                FOREIGN KEY (task_id) REFERENCES task(id)
            );

            CREATE INDEX IF NOT EXISTS idx_task_session ON task(session_id);
            CREATE INDEX IF NOT EXISTS idx_task_status ON task(status);
            CREATE INDEX IF NOT EXISTS idx_task_created ON task(created_at);
        """)
        try:
            conn.execute("ALTER TABLE task ADD COLUMN progress INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE task ADD COLUMN activity_message TEXT")
        except sqlite3.OperationalError:
            pass
    return path
