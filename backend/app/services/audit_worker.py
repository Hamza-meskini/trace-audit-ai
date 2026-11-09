"""Persistent local audit worker backed by the audit progress database."""

from __future__ import annotations

import asyncio
import logging
import uuid

from app.config import settings
from app.database import get_db_context
from app.models.project import Project
from app.services import audit_progress
from app.services.audit_progress import AuditCancelled
from app.services.llm_client import LLMUnavailableError
from app.services.pipeline import run_audit_pipeline


logger = logging.getLogger("traceaudit.audit_worker")
_worker_tasks: list[asyncio.Task] = []
_wake_event: asyncio.Event | None = None

__all__ = ["AuditCancelled", "start", "stop", "wake"]


def wake() -> None:
    if _wake_event is not None:
        _wake_event.set()


async def _set_project_status(project_id: str, status: str) -> None:
    """Replace the in-progress project status after a run stops early."""
    try:
        async with get_db_context() as db:
            project = await db.get(Project, project_id)
            if project is not None:
                project.status = status
    except Exception:
        logger.exception("Could not record status %r for project %s", status, project_id)


def _completion_message(result: dict) -> str:
    failed = result.get("failed_requirements") or []
    if not failed:
        return "Audit results saved"
    return (
        f"Audit finished, but {len(failed)} requirement(s) could not be assessed and kept "
        f"their previous results: {', '.join(failed[:10])}{' …' if len(failed) > 10 else ''}. "
        "Retry the audit to assess them."
    )


CANCEL_POLL_SECONDS = 1.0


async def _run_cancellable(project_id: str, work) -> dict:
    """REL-08: run the pipeline so a cancel stops spending within about a second.

    Progress callbacks only notice a cancel between stages; extraction and
    in-flight model calls never see it. A watcher cancels the task instead,
    which aborts in-flight HTTP calls. Database commits are shielded in the
    pipeline, so a cancel never interrupts a save halfway.
    """
    task = asyncio.create_task(work())
    cancelled_by_user = False
    try:
        while True:
            done, _ = await asyncio.wait({task}, timeout=CANCEL_POLL_SECONDS)
            if done:
                break
            if await asyncio.to_thread(audit_progress.is_cancel_requested, project_id):
                cancelled_by_user = True
                task.cancel()
                try:
                    await task
                except BaseException:
                    pass
                raise AuditCancelled()
    except asyncio.CancelledError:
        if not task.done():
            task.cancel()
        raise
    if task.cancelled() and cancelled_by_user:
        raise AuditCancelled()
    return task.result()


async def _execute(job: dict) -> None:
    project_id = job["project_id"]
    run_id = job["run_id"]

    def progress(stage, completed=0, total=0, message=""):
        if audit_progress.is_cancel_requested(project_id):
            raise AuditCancelled()
        audit_progress.update(project_id, stage, completed, total, message)

    async def pipeline() -> dict:
        async with get_db_context() as db:
            return await run_audit_pipeline(
                project_id,
                db,
                model=job.get("model"),
                thinking_level=job.get("thinking_level"),
                progress=progress,
                run_id=run_id,
            )

    try:
        result = await _run_cancellable(project_id, pipeline)
        audit_progress.update(
            project_id,
            "complete",
            message=_completion_message(result),
            status="complete",
            result=result,
        )
    except AuditCancelled:
        await _set_project_status(project_id, "Analysis cancelled")
        audit_progress.update(
            project_id,
            "cancelled",
            message="Audit cancelled; completed checkpoints were retained",
            status="cancelled",
        )
    except LLMUnavailableError as exc:
        logger.error("Audit for %s stopped: %s", project_id, exc)
        await _set_project_status(project_id, "Analysis stopped: model unavailable")
        audit_progress.update(
            project_id,
            "failed",
            status="failed",
            error=exc.user_message,
            error_kind=f"llm_{exc.kind}",
        )
    except asyncio.CancelledError:
        logger.info("Audit worker stopped while processing %s; the run will resume at startup", project_id)
        raise
    except Exception:
        logger.exception("Background audit failed for %s", project_id)
        await _set_project_status(project_id, "Analysis failed")
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
