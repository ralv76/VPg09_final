"""WebSocket для прогресса генерации. ТЗ 3.4, 7.1."""
import json
import logging
import time

from flask_sock import Sock
from backend.database import get_connection

logger = logging.getLogger(__name__)
sock = Sock()


def _get_task_status(task_id: str):
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM task WHERE id = ?", (task_id,)).fetchone()
        if not row:
            return None
        r = dict(row)
        if r.get("result_id"):
            res = conn.execute("SELECT title, duration_seconds FROM result WHERE id = ?", (r["result_id"],)).fetchone()
            if res:
                r["result"] = dict(res)
        if r.get("progress") is None:
            r["progress"] = {"extract": 15, "script": 40, "tts": 70, "music_cover": 85, "rss": 95, "done": 100}.get((r.get("stage") or "").lower(), 0)
        return r


@sock.route("/ws")
def progress_ws(ws):
    """Клиент отправляет {\"task_id\": \"...\"}; сервер шлёт обновления stage/status пока задача не завершена."""
    try:
        data = ws.receive()
        msg = json.loads(data) if isinstance(data, str) and data.strip() else {}
        task_id = (msg.get("task_id") or "").strip()
        if not task_id:
            ws.send(json.dumps({"error": "task_id required"}))
            return
        last_status = None
        while True:
            info = _get_task_status(task_id)
            if not info:
                ws.send(json.dumps({"error": "task not found"}))
                break
            status = info.get("status")
            stage = info.get("stage") or ""
            progress = info.get("progress")
            if progress is None:
                progress = {"extract": 15, "script": 40, "tts": 70, "music_cover": 85, "rss": 95, "done": 100}.get(stage.lower(), 0)
            payload = {"task_id": task_id, "status": status, "stage": stage, "progress": progress, "activity_message": info.get("activity_message") or ""}
            if info.get("error_message"):
                payload["error_message"] = info["error_message"]
            if info.get("result"):
                payload["result"] = info["result"]
            if payload != last_status:
                ws.send(json.dumps(payload))
                last_status = payload
            if status in ("completed", "failed", "cancelled"):
                break
            time.sleep(0.5)
    except Exception as e:
        logger.exception("ws error: %s", e)
        try:
            ws.send(json.dumps({"error": str(e)}))
        except Exception:
            pass
