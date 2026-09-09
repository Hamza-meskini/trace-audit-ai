"""Durable local audit job status, independent of the audit DB transaction.

This registry targets the current single-worker deployment. A separate SQLite
file allows status reads while the pipeline holds a write transaction on the
application database. It is not a distributed task queue.
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


def start(project_id, model):
    now = datetime.now(timezone.utc).isoformat()
    with _connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute("SELECT payload FROM runs WHERE project_id = ?", (project_id,)).fetchone()
        if row and json.loads(row[0])["status"] in ("queued", "running"):
            raise ValueError("An audit is already running for this project")
        payload = {"run_id": str(uuid.uuid4()), "project_id": project_id, "model": model,
                   "status": "queued", "stage": "queued", "completed": 0, "total": 0,
                   "message": "Waiting for the local audit worker", "started_at": now,
                   "updated_at": now, "events": [], "result": None, "error": None}
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


def recover_interrupted():
    """Called at startup of the single local worker; never claim stale runs completed."""
    with _connect() as connection:
        rows = connection.execute("SELECT project_id, payload FROM runs").fetchall()
        for project_id, raw in rows:
            payload = json.loads(raw)
            if payload["status"] in ("queued", "running"):
                payload.update(status="interrupted", error="Server restarted before this audit completed. Start a new run.")
                connection.execute("UPDATE runs SET payload=? WHERE project_id=?", (json.dumps(payload), project_id))
