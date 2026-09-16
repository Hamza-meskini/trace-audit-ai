"""Persistent local audit worker backed by the audit progress database."""

from __future__ import annotations

import asyncio
import logging
import uuid

from app.config import settings
from app.database import async_session
from app.services import audit_progress
from app.services.pipeline import run_audit_pipeline


logger = logging.getLogger("traceaudit.audit_worker")
_worker_tasks: list[asyncio.Task] = []
_wake_event: asyncio.Event | None = None


class AuditCancelled(Exception):
    pass


def wake() -> None:
    if _wake_event is not None:
        _wake_event.set()


async def _execute(job: dict) -> None:
    project_id = job["project_id"]
    run_id = job["run_id"]

    def progress(stage, completed=0, total=0, message=""):
        if audit_progress.is_cancel_requested(project_id):
            raise AuditCancelled()
        audit_progress.update(project_id, stage, completed, total, message)

    try:
        async with async_session() as db:
            result = await run_audit_pipeline(
                project_id,
                db,
                model=job.get("model"),
                thinking_level=job.get("thinking_level"),
                progress=progress,
                run_id=run_id,
            )
        audit_progress.update(
            project_id,
            "complete",
            message="Audit results saved",
            status="complete",
            result=result,
        )
    except AuditCancelled:
        audit_progress.update(
            project_id,
            "cancelled",
            message="Audit cancelled; completed checkpoints were retained",
            status="cancelled",
        )
    except asyncio.CancelledError:
        logger.info("Audit worker stopped while processing %s; the run will resume at startup", project_id)
        raise
    except Exception:
        logger.exception("Background audit failed for %s", project_id)
        audit_progress.update(
            project_id,
            "failed",
            status="failed",
            error="The audit failed. Completed checkpoints and previous results were retained. Check server logs and retry.",
        )


async def _run(worker_number: int) -> None:
    worker_id = f"local-{worker_number}-{uuid.uuid4()}"
    while True:
        job = await asyncio.to_thread(audit_progress.claim_next, worker_id)
        if job is not None:
            await _execute(job)
            continue
        assert _wake_event is not None
        _wake_event.clear()
        try:
            await asyncio.wait_for(
                _wake_event.wait(),
                timeout=max(0.1, float(settings.AUDIT_WORKER_POLL_SECONDS)),
            )
        except asyncio.TimeoutError:
            pass


def start() -> None:
    global _worker_tasks, _wake_event
    if _wake_event is None:
        _wake_event = asyncio.Event()
    _worker_tasks = [task for task in _worker_tasks if not task.done()]
    desired = max(1, int(settings.AUDIT_WORKER_CONCURRENCY))
    for worker_number in range(len(_worker_tasks) + 1, desired + 1):
        _worker_tasks.append(asyncio.create_task(
            _run(worker_number),
            name=f"traceaudit-audit-worker-{worker_number}",
        ))


async def stop() -> None:
    global _worker_tasks, _wake_event
    if not _worker_tasks:
        return
    for task in _worker_tasks:
        task.cancel()
    await asyncio.gather(*_worker_tasks, return_exceptions=True)
    _worker_tasks = []
    _wake_event = None
