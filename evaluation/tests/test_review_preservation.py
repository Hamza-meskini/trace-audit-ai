"""TRU-01: human review decisions survive a re-audit unless its basis changed.

Also covers the verdict-logic cache key (ACC-01): a change to the deterministic
verdict logic must miss the verification cache without touching other caches.
"""
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))
from app.config import settings
from app.services import pipeline
from app.services.classification import EvidenceLinkAssessment, RequirementAssessment


def _req(review_state, history, coverage="Supported"):
    return SimpleNamespace(
        review_state=review_state,
        coverage_status=coverage,
        extracted_parameters={"review_history": history},
    )


def _assessment(status="Supported", chunks=("c1", "c2"), review_state="Reviewed"):
    return RequirementAssessment(
        coverage_status=status,
        confidence=90.0,
        review_state=review_state,
        ai_analysis="",
        ai_recommendation="",
        evidence_links=[
            EvidenceLinkAssessment(chunk_id=c, document_name="d", page_number=1, quote="q",
                                   status="Supports requirement", label="E")
            for c in chunks
        ],
    )


APPROVED = {"id": "ev1", "action": "Approved", "reviewer": "Ana", "comment": "ok"}


def test_approval_kept_when_verdict_and_evidence_unchanged():
    req = _req("Approved", [APPROVED])
    assessment = _assessment(review_state="Needs review")
    state, event = pipeline._carry_human_review(req, assessment, {"c1", "c2"})
    assert state == "Approved"
    assert event is None
    assert assessment.pipeline_diagnostics["human_review_preserved"]["event_id"] == "ev1"


def test_comment_after_approval_does_not_hide_it():
    req = _req("Approved", [APPROVED, {"id": "ev2", "action": "Comment", "comment": "note"}])
    state, event = pipeline._carry_human_review(req, _assessment(), {"c1", "c2"})
    assert (state, event) == ("Approved", None)


def test_changed_verdict_sends_back_to_review_and_records_why():
    req = _req("Approved", [APPROVED])
    state, event = pipeline._carry_human_review(req, _assessment(status="Conflict"), {"c1", "c2"})
    assert state == "Needs review"
    assert event["system"] is True
    assert event["previous_review_state"] == "Approved"
    assert event["invalidated_event_id"] == "ev1"
    assert "Supported -> Conflict" in event["comment"]
    assert "Ana" in event["comment"]


def test_changed_evidence_sends_back_to_review():
    req = _req("Rejected", [{**APPROVED, "action": "Rejected"}])
    state, event = pipeline._carry_human_review(req, _assessment(chunks=("c1", "c9")), {"c1", "c2"})
    assert state == "Needs review"
    assert "1 added, 1 removed" in event["comment"]


def test_machine_state_without_human_decision_takes_the_new_assessment():
    req = _req("Reviewed", [])  # auto-closed by the gate, never reviewed by a person
    state, event = pipeline._carry_human_review(req, _assessment(review_state="Needs review"), {"c1"})
    assert (state, event) == ("Needs review", None)


def test_keep_under_review_is_not_auto_closed():
    req = _req("Needs review", [{**APPROVED, "action": "Needs review"}])
    state, event = pipeline._carry_human_review(
        req, _assessment(status="Supported", chunks=("c7",), review_state="Reviewed"), {"c1"})
    assert (state, event) == ("Needs review", None)


def test_system_event_ends_the_carry_over():
    system = {"id": "s1", "action": "Needs review", "system": True}
    req = _req("Needs review", [APPROVED, system])
    state, event = pipeline._carry_human_review(req, _assessment(review_state="Reviewed"), {"c1"})
    assert (state, event) == ("Reviewed", None)


def test_verdict_logic_version_changes_only_the_verification_key(monkeypatch):
    item = {"req_code": "REQ-1", "candidate_chunks": [{"id": "c1", "content": "x", "metadata": {}}]}
    key = lambda: pipeline._verification_fingerprint(  # noqa: E731
        item, corpus_fingerprint="corpus", model="m", thinking_level="HIGH")
    before = key()
    assert key() == before
    monkeypatch.setattr(settings, "VERDICT_LOGIC_VERSION", "next")
    assert key() != before


# ---- wiring: the real persist path on an in-memory database -----------------
import asyncio  # noqa: E402

from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from app.database import Base  # noqa: E402
from app.models.document import Document, EvidenceChunk  # noqa: E402
from app.models.finding import Finding  # noqa: E402
from app.models.project import Project  # noqa: E402
from app.models.requirement import Requirement, RequirementEvidence  # noqa: E402


async def _round_trip(second_status: str, second_chunks: tuple[str, ...]):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as db:
        db.add(Project(id="p1", name="P", product_name="U", audit_id="A1"))
        db.add(Document(id="d1", project_id="p1", filename="s.pdf", original_filename="s.pdf",
                        storage_path="s.pdf", page_count=1, processing_status="Indexed"))
        db.add_all([EvidenceChunk(id=c, document_id="d1", chunk_index=i, page_number=1, content=c, metadata_json={})
                    for i, c in enumerate(("c1", "c2", "c9"))])
        db.add(Requirement(id="r1", project_id="p1", req_code="REQ-1", title="T", source_document="s.pdf",
                           coverage_status="Unknown", extracted_parameters={}))
        await db.commit()

    async def persist(status, chunks):
        async with sessions() as db:
            await pipeline._persist_requirement_assessment(
                db, requirement_id="r1", project_id="p1",
                assessment=_assessment(status=status, chunks=chunks, review_state="Needs review"),
                item={"candidate_chunks": []}, fingerprint="f", ordinal=1, run_id="run",
                model="m", thinking_level="HIGH")
            await db.commit()

    await persist("Conflict", ("c1", "c2"))
    async with sessions() as db:  # a reviewer approves, as api/requirements.save_review does
        req = await db.get(Requirement, "r1")
        req.review_state = "Approved"
        req.extracted_parameters = {**req.extracted_parameters, "review_history": [APPROVED]}
        for finding in (await db.execute(select(Finding))).scalars():
            finding.review_state = "Approved"
        await db.commit()
    await persist(second_status, second_chunks)
    async with sessions() as db:
        req = await db.get(Requirement, "r1")
        findings = (await db.execute(select(Finding))).scalars().all()
        links = (await db.execute(select(RequirementEvidence.evidence_chunk_id))).scalars().all()
        result = (req.review_state, [f.review_state for f in findings], sorted(links),
                  req.extracted_parameters.get("review_history"))
    await engine.dispose()
    return result


def test_persist_keeps_approval_through_unchanged_reaudit():
    state, finding_states, links, history = asyncio.run(_round_trip("Conflict", ("c1", "c2")))
    assert state == "Approved"
    assert finding_states == ["Approved"]
    assert links == ["c1", "c2"]
    assert history == [APPROVED]


def test_persist_reopens_review_when_reaudit_changes_evidence():
    state, finding_states, links, history = asyncio.run(_round_trip("Conflict", ("c1", "c9")))
    assert state == "Needs review"
    assert finding_states == ["Needs review"]
    assert links == ["c1", "c9"]
    assert [event["id"] for event in history][0] == "ev1"
    assert history[-1]["system"] is True and history[-1]["previous_review_state"] == "Approved"
