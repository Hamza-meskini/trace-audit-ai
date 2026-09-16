"""Durable local audit job status, independent of the audit DB transaction.

This registry targets the current single-host worker pool. A separate SQLite
file allows status reads while the pipeline holds a write transaction on the
application database. It survives local process restarts, but it is not a
cross-host distributed task queue.
"""
import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from app.config import settings


@contextmanager
def _connect():
    root = Path(settings.UPLOAD_DIR)
    root.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(root / ".audit-progress.sqlite3", timeout=5)
    try:
        connection.execute("CREATE TABLE IF NOT EXISTS runs (project_id TEXT PRIMARY KEY, payload TEXT NOT NULL)")
        with connection:
            yield connection
    finally:
        connection.close()


def latest(project_id):
    with _connect() as connection:
        row = connection.execute("SELECT payload FROM runs WHERE project_id = ?", (project_id,)).fetchone()
    return json.loads(row[0]) if row else None


def start(project_id, model, thinking_level=None):
    now = datetime.now(timezone.utc).isoformat()
    with _connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute("SELECT payload FROM runs WHERE project_id = ?", (project_id,)).fetchone()
        if row and json.loads(row[0])["status"] in ("queued", "running", "cancelling"):
            raise ValueError("An audit is already running for this project")
        payload = {"run_id": str(uuid.uuid4()), "project_id": project_id, "model": model,
                   "thinking_level": thinking_level,
                   "status": "queued", "stage": "queued", "completed": 0, "total": 0,
                   "message": "Waiting for the local audit worker", "started_at": now,
                   "updated_at": now, "events": [], "result": None, "error": None,
                   "cancel_requested": False, "attempt": 0, "worker_id": None}
        connection.execute("INSERT OR REPLACE INTO runs VALUES (?, ?)", (project_id, json.dumps(payload)))
    return payload


def update(project_id, stage, completed=0, total=0, message="", **extra):
    with _connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute("SELECT payload FROM runs WHERE project_id = ?", (project_id,)).fetchone()
        if not row:
            return
        payload = json.loads(row[0])
        now = datetime.now(timezone.utc).isoformat()
        event = {"stage": stage, "completed": completed, "total": total, "message": message, "at": now}
        payload.update({**event, "updated_at": now, "status": "running", **extra})
        payload["events"] = [*payload["events"][-99:], event]
        connection.execute("UPDATE runs SET payload=? WHERE project_id=?", (json.dumps(payload), project_id))


def claim_next(worker_id):
    """Atomically claim the oldest queued audit across worker processes."""
    with _connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        rows = connection.execute("SELECT project_id, payload FROM runs").fetchall()
        queued = []
        for project_id, raw in rows:
            payload = json.loads(raw)
            if payload.get("status") == "queued":
                queued.append((payload.get("started_at", ""), project_id, payload))
        if not queued:
            return None
        _, project_id, payload = min(queued, key=lambda item: item[0])
        now = datetime.now(timezone.utc).isoformat()
        payload.update(
            status="running",
            stage="queued",
            worker_id=worker_id,
            attempt=int(payload.get("attempt") or 0) + 1,
            updated_at=now,
            message="Audit worker started the run",
        )
        payload["events"] = [
            *payload.get("events", [])[-99:],
            {"stage": "queued", "completed": 0, "total": 0,
             "message": "Audit worker started the run", "at": now},
        ]
        connection.execute(
            "UPDATE runs SET payload=? WHERE project_id=?",
            (json.dumps(payload), project_id),
        )
    return payload


def request_cancel(project_id):
    with _connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute("SELECT payload FROM runs WHERE project_id=?", (project_id,)).fetchone()
        if not row:
            return None
        payload = json.loads(row[0])
        if payload.get("status") not in ("queued", "running", "cancelling"):
            return payload
        now = datetime.now(timezone.utc).isoformat()
        if payload.get("status") == "queued":
            payload.update(status="cancelled", stage="cancelled", message="Audit cancelled before it started")
        else:
            payload.update(status="cancelling", message="Cancellation requested; finishing the current operation")
        payload.update(cancel_requested=True, updated_at=now)
        payload["events"] = [
            *payload.get("events", [])[-99:],
            {"stage": payload["stage"], "completed": payload.get("completed", 0),
             "total": payload.get("total", 0), "message": payload["message"], "at": now},
        ]
        connection.execute("UPDATE runs SET payload=? WHERE project_id=?", (json.dumps(payload), project_id))
    return payload


def is_cancel_requested(project_id):
    payload = latest(project_id)
    return bool(payload and payload.get("cancel_requested"))


def recover_interrupted(requeue=False):
    """Recover local work after restart; never claim an unfinished run completed."""
    with _connect() as connection:
        rows = connection.execute("SELECT project_id, payload FROM runs").fetchall()
        for project_id, raw in rows:
            payload = json.loads(raw)
            if payload["status"] in ("running", "cancelling"):
                if requeue and not payload.get("cancel_requested"):
                    payload.update(
                        status="queued",
                        stage="queued",
                        worker_id=None,
                        error=None,
                        message="Server restarted; resuming from saved audit checkpoints",
                    )
                else:
                    payload.update(
                        status="interrupted",
                        error="Server restarted before this audit completed. Start a new run.",
                    )
                connection.execute("UPDATE runs SET payload=? WHERE project_id=?", (json.dumps(payload), project_id))
