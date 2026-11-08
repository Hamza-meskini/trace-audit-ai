"""Regressions for the 2026-11 hardening pass (offline; no live credentials or model calls)."""
import asyncio
import tempfile
import unittest
import uuid
from contextlib import ExitStack, asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI
from sqlalchemy import create_engine, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session

from app import database
from app.config import settings
from app.database import Base, configure_databricks_batch_inserts, get_db
from app.api.documents import router as documents_router
from app.api.projects import router as projects_router
from app.api.requirements import review_target_query, router as requirements_router
from app.models.document import Document, EvidenceChunk
from app.models.finding import Finding
from app.models.project import Project
from app.models.requirement import Requirement, RequirementEvidence
from app.services import audit_progress, audit_worker
from app.services.audit_progress import AuditCancelled


DATABRICKS_URL = "databricks://token:x@example.cloud.databricks.com?http_path=/sql/1.0/warehouses/w&catalog=c&schema=s"


def test_review_query_has_no_row_lock_on_databricks():
    from databricks.sqlalchemy import DatabricksDialect

    sql = str(review_target_query("p1", "r1").compile(dialect=DatabricksDialect()))
    assert "FOR UPDATE" not in sql.upper()


class _RecordingCursor:
    def __init__(self, log):
        self.log = log
        self.description = None
        self.rowcount = -1

    def execute(self, sql, params=None, **_kw):
        self.log.append(("execute", sql, params))

    def executemany(self, sql, seq, **_kw):
        self.log.append(("executemany", sql, list(seq)))

    def fetchall(self):
        return []

    def fetchone(self):
        return None

    def close(self):
        pass


class _RecordingConnection:
    def __init__(self, log):
        self.log = log

    def cursor(self):
        return _RecordingCursor(self.log)

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


def _warehouse_requests_for_chunk_flush(rows: int, batched: bool) -> tuple[int, int]:
    """Return (warehouse requests, max parameters per statement) for an ORM flush."""
    log: list = []
    engine = create_engine(DATABRICKS_URL, creator=lambda: _RecordingConnection(log))
    if batched:
        configure_databricks_batch_inserts(engine)
    with Session(engine) as session:
        session.add(Project(id="p", name="n", audit_id="a", product_name="x"))
        session.add(Document(id="d", project_id="p", filename="f", original_filename="f", storage_path="x"))
        session.flush()
        log.clear()
        for index in range(rows):
            session.add(EvidenceChunk(id=str(uuid.uuid4()), document_id="d", page_number=1,
                                      chunk_index=index, content="text", metadata_json={}))
        session.flush()
    inserts = [entry for entry in log if entry[1].lstrip().upper().startswith("INSERT")]
    # The connector's executemany() sends one request per parameter set.
    requests = sum(len(params) if kind == "executemany" else 1 for kind, _sql, params in inserts)
    max_params = max((len(params) for kind, _sql, params in inserts
                      if kind == "execute" and isinstance(params, dict)), default=0)
    return requests, max_params


def test_databricks_batch_inserts_collapse_round_trips():
    unbatched, _ = _warehouse_requests_for_chunk_flush(200, batched=False)
    batched, max_params = _warehouse_requests_for_chunk_flush(200, batched=True)
    assert unbatched == 200
    assert batched <= 10
    assert 0 < max_params <= settings.DATABRICKS_INSERT_MAX_PARAMETERS


def test_sqlite_connections_use_wal_and_busy_timeout(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'wal.db').as_posix()}")
    from sqlalchemy import event

    event.listen(engine, "connect", database._configure_sqlite_connection)
    with engine.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA journal_mode").scalar().lower() == "wal"
        assert connection.exec_driver_sql("PRAGMA busy_timeout").scalar() == settings.SQLITE_BUSY_TIMEOUT_MS
    engine.dispose()


def test_init_db_adds_foreign_key_indexes_to_existing_sqlite_databases(tmp_path):
    async def run():
        engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'idx.db').as_posix()}")
        try:
            with patch.object(database, "engine", engine), patch.object(settings, "DATABASE_BACKEND", "sqlite"):
                await database.init_db()
                await database.init_db()  # idempotent on an existing database
            async with engine.connect() as connection:
                rows = (await connection.execute(text(
                    "SELECT name FROM sqlite_master WHERE type = 'index' AND name LIKE 'ix_%'"
                ))).scalars().all()
        finally:
            await engine.dispose()
        assert {name for name, _table, _column in database.SQLITE_INDEXES} <= set(rows)
    asyncio.run(run())


class HardeningApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.setting = patch.object(settings, "UPLOAD_DIR", self.temp.name)
        self.setting.start()
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        self.fail_commit = False
        app = FastAPI()
        app.include_router(projects_router, prefix="/api")
        app.include_router(documents_router, prefix="/api")
        app.include_router(requirements_router, prefix="/api")

        async def session_dependency():
            async with self.sessions() as db:
                yield db
                if self.fail_commit:
                    raise RuntimeError("warehouse commit failed")
                await db.commit()

        app.dependency_overrides[get_db] = session_dependency
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        )
        self.source = Path(self.temp.name) / "report.pdf"
        self.source.write_bytes(b"%PDF-1.4 fixture")
        async with self.sessions() as db:
            db.add(Project(id="p1", name="Fixture", product_name="Unit", audit_id="TA-2026-AAAA"))
            db.add(Document(id="d1", project_id="p1", filename="report.pdf", original_filename="report.pdf",
                            storage_path=str(self.source), processing_status="Indexed"))
            db.add(EvidenceChunk(id="c1", document_id="d1", chunk_index=0, page_number=1, content="Opening 20 ms"))
            db.add(Requirement(id="r1", project_id="p1", req_code="REQ-1", title="Opening",
                               coverage_status="Supported", review_state="Reviewed", sources_count=1))
            db.add(RequirementEvidence(id="l1", requirement_id="r1", evidence_chunk_id="c1"))
            await db.commit()

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.engine.dispose()
        self.setting.stop()
        self.temp.cleanup()

    async def test_review_save_sends_no_row_lock_to_the_warehouse(self):
        from databricks.sqlalchemy import DatabricksDialect
        from sqlalchemy.ext.asyncio import AsyncSession

        executed = []
        original = AsyncSession.execute

        async def recording_execute(session, statement, *args, **kwargs):
            executed.append(statement)
            return await original(session, statement, *args, **kwargs)

        with patch.object(AsyncSession, "execute", recording_execute):
            response = await self.client.post("/api/projects/p1/requirements/r1/reviews", json={
                "action": "Approved", "reviewer": "Reviewer", "comment": "Checked the report",
            })
        self.assertEqual(response.status_code, 200)
        compiled = [str(statement.compile(dialect=DatabricksDialect()))
                    for statement in executed if hasattr(statement, "compile")]
        self.assertTrue(compiled)
        self.assertFalse([sql for sql in compiled if "FOR UPDATE" in sql.upper()])

    async def test_commit_failure_is_reported_instead_of_a_false_success(self):
        self.fail_commit = True
        response = await self.client.patch("/api/projects/p1", json={"name": "Renamed"})
        self.assertEqual(response.status_code, 500)

    async def test_deleting_a_document_removes_its_links_and_reopens_review(self):
        response = await self.client.delete("/api/projects/p1/documents/d1")
        self.assertEqual(response.status_code, 204)
        async with self.sessions() as db:
            self.assertEqual((await db.execute(select(RequirementEvidence))).scalars().all(), [])
            self.assertEqual((await db.execute(select(EvidenceChunk))).scalars().all(), [])
            requirement = await db.get(Requirement, "r1")
        self.assertEqual(requirement.review_state, "Needs review")
        self.assertEqual(requirement.sources_count, 0)
        self.assertFalse(self.source.exists())

    async def test_failed_document_delete_keeps_the_original_file(self):
        self.fail_commit = True
        with patch("sqlalchemy.ext.asyncio.AsyncSession.commit", new=AsyncMock(side_effect=RuntimeError("down"))):
            response = await self.client.delete("/api/projects/p1/documents/d1")
        self.assertEqual(response.status_code, 500)
        self.assertTrue(self.source.exists())

    async def test_project_creation_skips_audit_ids_already_in_use(self):
        ids = iter(["TA-2026-AAAA", "TA-2026-BBBB"])
        with patch("app.api.projects._generate_audit_id", side_effect=lambda: next(ids)):
            response = await self.client.post("/api/projects", json={"name": "New", "product_name": "Unit"})
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["audit_id"], "TA-2026-BBBB")


class PipelineCancellationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.setting = patch.object(settings, "UPLOAD_DIR", self.temp.name)
        self.setting.start()
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        source = Path(self.temp.name) / "source.pdf"
        source.write_bytes(b"%PDF-1.4 fixture")
        async with self.sessions() as db:
            db.add(Project(id="p1", name="Fixture", product_name="Unit", audit_id="A1"))
            db.add(Document(id="d1", project_id="p1", filename="source.pdf", original_filename="source.pdf",
                            storage_path=str(source), processing_status="Indexed"))
            db.add(EvidenceChunk(id="c1", document_id="d1", chunk_index=0, page_number=1, content="Opening 20 ms"))
            for index in range(1, 7):
                db.add(Requirement(id=f"r{index}", project_id="p1", req_code=f"REQ-{index}", title="Opening",
                                   extracted_parameters={"conditions": []}))
            await db.commit()

    async def asyncTearDown(self):
        await self.engine.dispose()
        self.setting.stop()
        self.temp.cleanup()

    def _patched_stages(self, stack, assess):
        candidate = SimpleNamespace(chunk_id="c1", document_id="d1", document_name="source.pdf",
                                    doc_type="Test report", page_number=1, content="Opening 20 ms",
                                    document_profile=None, metadata={})
        stack.enter_context(patch("app.services.pipeline._cache_matches_source", return_value=True))
        for name, value in {
            "profile_documents": {}, "discover_specification_documents": ([], set()),
            "precompute_chunk_embeddings": {}, "describe_retrieved_figures": {},
            "retrieve_with_fallback": ([candidate], SimpleNamespace(as_dict=lambda: {})),
        }.items():
            stack.enter_context(patch("app.services.pipeline." + name, new=AsyncMock(return_value=value)))
        stack.enter_context(patch("app.services.pipeline.assess_requirement_coverage_async", new=assess))

    @staticmethod
    def _assessment():
        from app.services.classification import RequirementAssessment

        return RequirementAssessment(coverage_status="Supported", confidence=90, review_state="Needs review",
                                     ai_analysis="ok", ai_recommendation="ok")

    async def test_cancel_during_requirement_phase_drains_instead_of_failing_each_requirement(self):
        from app.services.pipeline import run_audit_pipeline

        assess = AsyncMock(return_value=self._assessment())

        def progress(stage, completed=0, *_args):
            # The user cancels once the first requirement has located its
            # evidence; from then on every requirement checkpoint raises, as
            # audit_worker's callback does. (completed == 0 is the shared
            # "Preparing evidence search" step before any requirement runs.)
            if stage == "retrieval" and completed >= 1:
                raise AuditCancelled()

        with ExitStack() as stack:
            self._patched_stages(stack, assess)
            retrieve = stack.enter_context(patch(
                "app.services.pipeline.retrieve_with_fallback",
                new=AsyncMock(return_value=([], SimpleNamespace(as_dict=lambda: {}))),
            ))
            async with self.sessions() as db:
                with self.assertNoLogs("traceaudit.pipeline", level="ERROR"):
                    with self.assertRaises(AuditCancelled):
                        await run_audit_pipeline("p1", db, model="test-model", progress=progress)
        # Previously the cancellation was caught as a per-requirement failure:
        # all six requirements still ran retrieval (remote AI Search and the
        # reranker in production) and each logged a failure stack trace.
        self.assertEqual(retrieve.await_count, 1)
        self.assertEqual(assess.await_count, 0)

    async def test_requirement_failures_make_the_result_partial(self):
        from app.services.pipeline import run_audit_pipeline

        async def assess(**kwargs):
            if kwargs["req_code"] == "REQ-2":
                raise RuntimeError("model endpoint unavailable")
            return self._assessment()

        with ExitStack() as stack:
            self._patched_stages(stack, AsyncMock(side_effect=assess))
            async with self.sessions() as db:
                result = await run_audit_pipeline("p1", db, model="test-model")
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["failed_requirements"], ["REQ-2"])
        async with self.sessions() as db:
            self.assertEqual((await db.get(Project, "p1")).status, "Analysis incomplete")
        self.assertIn("REQ-2", audit_worker._completion_message(result))

    async def test_worker_records_failed_and_cancelled_project_status(self):
        @asynccontextmanager
        async def context():
            async with self.sessions() as db:
                yield db
                await db.commit()

        for failure, expected in ((RuntimeError("boom"), "Analysis failed"),
                                  (AuditCancelled(), "Analysis cancelled")):
            job = audit_progress.start("p1", "test-model")
            with patch.object(audit_worker, "get_db_context", context), \
                    patch.object(audit_worker, "run_audit_pipeline", new=AsyncMock(side_effect=failure)):
                await audit_worker._execute(job)
            async with self.sessions() as db:
                self.assertEqual((await db.get(Project, "p1")).status, expected)
            self.assertIn(audit_progress.latest("p1")["status"], ("failed", "cancelled"))


def test_audit_worker_still_exports_the_cancellation_type():
    assert audit_worker.AuditCancelled is AuditCancelled
    assert issubclass(Finding, Base)
