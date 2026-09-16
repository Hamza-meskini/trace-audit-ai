"""UI data contracts, persistence and project-scoped source access (no LLM calls)."""
import tempfile
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch, AsyncMock

import fitz
import httpx
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.database import Base, get_db
from app.config import settings
from app.api.requirements import router as requirements_router
from app.api.documents import router as documents_router
from app.api.audit import router as audit_router
from app.models.project import Project
from app.models.document import Document, EvidenceChunk
from app.models.requirement import Requirement
from app.models.finding import Finding
from app.services import audit_progress


class ReviewWorkspaceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.setting = patch.object(settings, "UPLOAD_DIR", self.temp.name)
        self.setting.start()
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        app = FastAPI()
        app.include_router(requirements_router, prefix="/api")
        app.include_router(documents_router, prefix="/api")
        app.include_router(audit_router, prefix="/api")

        async def session_dependency():
            async with self.sessions() as db:
                yield db
                await db.commit()
        app.dependency_overrides[get_db] = session_dependency
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
        source = Path(self.temp.name) / "source.pdf"
        with fitz.open() as pdf:
            page = pdf.new_page()
            page.insert_text((50, 50), "REQ-1: The unit shall open within 20 ms.")
            page.draw_rect(fitz.Rect(50, 100, 250, 220))
            pdf.save(source)
        async with self.sessions() as db:
            db.add_all([Project(id="p1", name="Review fixture", product_name="Unit", audit_id="A1"),
                        Project(id="p2", name="Other project", product_name="Other", audit_id="A2")])
            db.add(Document(id="d1", project_id="p1", filename="source.pdf", original_filename="source.pdf",
                            storage_path=str(source), page_count=1, processing_status="Indexed"))
            db.add_all([
                EvidenceChunk(id="c1", document_id="d1", chunk_index=0, page_number=1,
                              content="REQ-1: The unit shall open within 20 ms.", metadata_json={"block_type": "text_section"}),
                EvidenceChunk(id="c2", document_id="d1", chunk_index=1, page_number=1,
                              content="| Test | Limit |\n| --- | --- |\n| Opening | 20 ms |", metadata_json={"block_type": "table"}),
                EvidenceChunk(id="c3", document_id="d1", chunk_index=2, page_number=1,
                              content="FIGURE: opening sequence", metadata_json={"block_type": "figure", "storage_path": "private"}),
            ])
            db.add(Requirement(id="r1", project_id="p1", req_code="REQ-1", title="Opening", source_document="source.pdf",
                               coverage_status="Supported", extracted_parameters={
                                   "conditions": [{"condition_id": "C1", "description": "Open within 20 ms"}],
                                   "logic": {"operator": "ALL_OF"}, "source_document_id": "d1",
                                   "contract_complete": False, "validation_issues": ["Source association needs review"],
                                   "verification": {"condition_results": [{"condition_id": "C1", "status": "PROVEN", "validation_state": "UNRESOLVED"}],
                                                    "diagnostics": {"review_gate": {"required": True, "reasons": ["Confirm test subject"]}}, "evidence_catalog": []},
                               }))
            await db.commit()

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.engine.dispose()
        self.setting.stop()
        self.temp.cleanup()

    async def test_source_context_includes_text_table_figure_and_conditions(self):
        response = await self.client.get("/api/projects/p1/requirements/r1")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["source_document_id"], "d1")
        self.assertEqual(len(data["source_blocks"]), 3)
        self.assertEqual(data["condition_results"][0]["status"], "PROVEN")
        self.assertFalse(data["contract_complete"])
        self.assertEqual(data["validation_issue_count"], 1)
        self.assertEqual(data["unresolved_condition_count"], 1)
        self.assertEqual(data["review_blocker_count"], 1)
        self.assertNotIn("storage_path", data["source_blocks"][2]["metadata"])

    async def test_review_survives_reload_without_rewriting_ai_verdict(self):
        response = await self.client.post("/api/projects/p1/requirements/r1/reviews", json={
            "action": "Rejected", "reviewer": "Reviewer", "comment": "Identity needs confirmation.",
        })
        self.assertEqual(response.status_code, 200)
        data = (await self.client.get("/api/projects/p1/requirements/r1")).json()
        self.assertEqual(data["review_state"], "Rejected")
        self.assertEqual(data["coverage_status"], "Supported")
        self.assertEqual(len(data["review_history"]), 1)
        self.assertEqual(len(data["condition_results"]), 1)

    async def test_human_verdict_is_versioned_separately_from_ai_verdict(self):
        response = await self.client.post("/api/projects/p1/requirements/r1/reviews", json={
            "action": "Reviewed", "reviewer": "Reviewer", "comment": "Test record is inconclusive.",
            "resolution_type": "Override verdict", "human_verdict": "Unknown",
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["human_verdict"], "Unknown")
        data = (await self.client.get("/api/projects/p1/requirements/r1")).json()
        self.assertEqual(data["coverage_status"], "Supported")
        self.assertEqual(data["human_verdict"], "Unknown")
        self.assertEqual(data["human_assessment"]["ai_verdict"], "Supported")

        listing = (await self.client.get("/api/projects/p1/requirements")).json()
        self.assertEqual(listing[0]["human_verdict"], "Unknown")

    async def test_original_page_and_inspection_are_project_scoped(self):
        self.assertEqual((await self.client.get("/api/projects/p2/documents/d1/file")).status_code, 404)
        self.assertEqual((await self.client.get("/api/projects/p2/documents/d1/pages/1.png")).status_code, 404)
        response = await self.client.get("/api/projects/p1/documents/d1/pages/1.png")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.content.startswith(b"\x89PNG"))
        self.assertEqual((await self.client.get("/api/projects/p1/documents/d1/pages/2.png")).status_code, 404)
        data = (await self.client.get("/api/projects/p1/documents/d1/inspection")).json()
        self.assertEqual(data["counts"], {"blocks": 3, "tables": 1, "figures": 1})

    async def test_running_audit_protects_review_and_document_mutations(self):
        audit_progress.start("p1", "test-model")
        response = await self.client.post("/api/projects/p1/requirements/r1/reviews", json={
            "action": "Approved", "reviewer": "Reviewer", "comment": "Reviewed sources",
        })
        self.assertEqual(response.status_code, 409)
        self.assertEqual((await self.client.delete("/api/projects/p1/documents/d1")).status_code, 409)
        response = await self.client.post("/api/projects/p1/documents", files={
            "file": ("new.pdf", b"test", "application/pdf"),
        })
        self.assertEqual(response.status_code, 409)
        cancelled = await self.client.post("/api/projects/p1/audit/cancel")
        self.assertEqual(cancelled.status_code, 200)
        self.assertEqual(cancelled.json()["status"], "cancelled")

    async def test_pipeline_persists_condition_provenance_and_keeps_review_history(self):
        from app.services.pipeline import run_audit_pipeline
        from app.services.classification import RequirementAssessment
        from app.schemas.verification_result import ConditionVerificationResult

        await self.client.post("/api/projects/p1/requirements/r1/reviews", json={
            "action": "Comment", "reviewer": "Reviewer", "comment": "Before rerun",
        })
        candidate = SimpleNamespace(chunk_id="c2", document_id="d1", document_name="source.pdf",
                                    doc_type="Test report", page_number=1, content="Opening 20 ms",
                                    document_profile=None, metadata={"block_type": "table"})
        assessment = RequirementAssessment(
            coverage_status="Supported", confidence=92, review_state="Needs review",
            ai_analysis="Test reasoning", ai_recommendation="Inspect source",
            condition_results=[ConditionVerificationResult(condition_id="C1", status="PROVEN",
                                                           evidence_ids=["E1"], quote="Opening 20 ms")],
            pipeline_diagnostics={"review_gate": {"required": True, "reasons": ["Test review reason"]}},
        )
        progress = []
        with ExitStack() as stack:
            stack.enter_context(patch("app.services.pipeline._cache_matches_source", return_value=True))
            for name, value in {
                "profile_documents": {}, "discover_specification_documents": ([], set()),
                "precompute_chunk_embeddings": {}, "retrieve_candidate_evidence_hybrid": [candidate],
                "rerank_candidates": ([candidate], {"enabled": True, "used": True}),
                "describe_retrieved_figures": {}, "batch_assess_requirements": {"REQ-1": assessment},
            }.items():
                stack.enter_context(patch("app.services.pipeline." + name, new=AsyncMock(return_value=value)))
            async with self.sessions() as db:
                await run_audit_pipeline("p1", db, model="test-model", progress=lambda *args: progress.append(args))
        data = (await self.client.get("/api/projects/p1/requirements/r1")).json()
        self.assertEqual(data["condition_results"][0]["quote"], "Opening 20 ms")
        self.assertEqual(data["diagnostics"]["evidence_catalog"][0]["chunk_id"], "c2")
        self.assertEqual(data["diagnostics"]["model"], "test-model")
        self.assertTrue(data["diagnostics"]["assessed_at"])
        self.assertEqual(len(data["review_history"]), 1)
        self.assertIn("reasoning", [event[0] for event in progress])
        self.assertIn("saving", [event[0] for event in progress])

    async def test_job_start_returns_accepted_and_prevents_duplicate(self):
        response = await self.client.post("/api/projects/p1/audit", json={"model": "test-model"})
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["status"], "queued")
        self.assertEqual((await self.client.post("/api/projects/p1/audit", json={})).status_code, 409)
        self.assertEqual((await self.client.post("/api/projects/p2/audit", json={})).status_code, 422)
        audit_progress.update("p1", "reasoning", 3, 12, "3 requirements evaluated")
        data = (await self.client.get("/api/projects/p1/audit")).json()
        self.assertEqual((data["completed"], data["total"]), (3, 12))
        audit_progress.recover_interrupted()
        self.assertEqual(audit_progress.latest("p1")["status"], "interrupted")
        audit_progress.start("p1", "test-model")
        audit_progress.update("p1", "complete", status="complete", result={"status": "success"})
        self.assertEqual(audit_progress.latest("p1")["status"], "complete")

    async def test_durable_job_can_resume_or_cancel(self):
        queued = audit_progress.start("p1", "test-model", "LOW")
        claimed = audit_progress.claim_next("worker-1")
        self.assertEqual(claimed["run_id"], queued["run_id"])
        self.assertEqual(claimed["thinking_level"], "LOW")
        self.assertEqual(claimed["attempt"], 1)

        audit_progress.recover_interrupted(requeue=True)
        self.assertEqual(audit_progress.latest("p1")["status"], "queued")
        claimed = audit_progress.claim_next("worker-2")
        self.assertEqual(claimed["attempt"], 2)

        cancelling = audit_progress.request_cancel("p1")
        self.assertEqual(cancelling["status"], "cancelling")
        self.assertTrue(audit_progress.is_cancel_requested("p1"))
