"""REL-05 call ledger, REL-06 shared provider limits, EVA-03 recordings."""
import asyncio
import json
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "evaluation"))

from app.config import settings  # noqa: E402
from app.services import call_ledger, llm_client, llm_recording, request_limits  # noqa: E402


class Answer(BaseModel):
    status: str
    note: str = ""


def _fake_databricks(monkeypatch, responses):
    monkeypatch.setattr(settings, "DATABRICKS_TOKEN", "test")
    monkeypatch.setattr(settings, "DATABRICKS_BASE_URL", "https://example.invalid")
    client = AsyncMock()
    client.post.side_effect = responses
    context = AsyncMock()
    context.__aenter__.return_value = client
    monkeypatch.setattr(llm_client.httpx, "AsyncClient", lambda **kwargs: context)
    return client


def _ok(content, served="llama-4-maverick-served"):
    response = MagicMock(status_code=200)
    response.json.return_value = {"model": served, "choices": [{"message": {"content": content}}]}
    return response


# ── REL-05 ──────────────────────────────────────────────────────────────────

def test_ledger_scopes_nest_and_summaries_count_models_and_fallbacks():
    call_ledger.record({"kind": "call", "model": "ignored"})  # outside any scope: no-op
    with call_ledger.recording() as run:
        with call_ledger.recording() as requirement:
            call_ledger.record({"kind": "call", "model": "a", "served_model": "a-1", "status": "ok"})
            with call_ledger.fallback_from("a"):
                call_ledger.record({"kind": "call", "model": "b", "status": "empty"})
        call_ledger.record({"kind": "cache_hit", "model": "a"})
    assert len(requirement) == 2 and len(run) == 3
    summary = call_ledger.summarize(run)
    assert summary["calls"] == 2 and summary["cache_hits"] == 1
    assert summary["served_models"] == {"a-1": 1}
    assert summary["statuses"] == {"empty": 1, "ok": 1}
    assert summary["fallbacks"] == [{"from": "a", "to": "b", "status": "empty"}]


def test_databricks_call_lands_in_the_ledger_with_its_served_model(monkeypatch):
    _fake_databricks(monkeypatch, [_ok('{"status":"PROVEN"}')])
    with call_ledger.recording() as calls:
        result = asyncio.run(llm_client.call_databricks_chat_completions("p", json_mode=True))
    assert result == '{"status":"PROVEN"}'
    (entry,) = calls
    assert entry["status"] == "ok" and entry["served_model"] == "llama-4-maverick-served"
    assert entry["model"] == "system.ai.llama-4-maverick" and entry["attempts"] == 1
    assert "prompt" not in entry and "response" not in entry  # metadata only


# ── REL-06 ──────────────────────────────────────────────────────────────────

def test_retry_after_pauses_every_caller_of_that_model_only():
    key = request_limits.provider_key("databricks", "M")
    request_limits.note_rate_limited(key, 0.15)
    request_limits.note_rate_limited(key, 0.01)  # a shorter delay never shortens the cooldown

    async def go():
        started = time.monotonic()
        other = await request_limits.wait_for_provider(request_limits.provider_key("databricks", "other"))
        waited = await request_limits.wait_for_provider(key)
        return other, waited, time.monotonic() - started

    other, waited, elapsed = asyncio.run(go())
    assert other == 0.0 and waited > 0.1 and elapsed > 0.1


def test_optional_pacing_spaces_requests(monkeypatch):
    monkeypatch.setattr(settings, "LLM_REQUESTS_PER_MINUTE", 1200)  # one every 50 ms
    monkeypatch.setattr(settings, "LLM_REQUEST_BURST", 1)
    key = request_limits.provider_key("databricks", "paced")

    async def go():
        started = time.monotonic()
        waits = [await request_limits.wait_for_provider(key) for _ in range(3)]
        return waits, time.monotonic() - started

    waits, elapsed = asyncio.run(go())
    # Slots are absolute (0, 50, 100 ms), so oversleeping one shortens the next wait.
    assert waits[0] == 0.0 and waits[1] > 0 and elapsed >= 0.08


def test_a_429_sets_the_shared_cooldown(monkeypatch):
    refused = MagicMock(status_code=429)
    refused.headers = {"retry-after": "7"}
    _fake_databricks(monkeypatch, [refused, _ok('{"status":"PROVEN"}')])
    monkeypatch.setattr(llm_client.asyncio, "sleep", AsyncMock())
    asyncio.run(llm_client.call_databricks_chat_completions("p", json_mode=True))
    assert request_limits.cooldown_remaining(request_limits.provider_key("databricks", "system.ai.llama-4-maverick")) > 6


# ── EVA-03 ──────────────────────────────────────────────────────────────────

def test_recording_is_refused_inside_the_checkout(monkeypatch):
    monkeypatch.setattr(settings, "LLM_RECORD_DIR", str(llm_recording.REPO_ROOT / "evaluation" / "recordings"))
    assert llm_recording.record_dir() is None


def _record(monkeypatch, tmp_path, prompt="Does C1 hold?"):
    # pytest's basetemp sits inside this checkout; stand in for an outside directory.
    monkeypatch.setattr(llm_recording, "REPO_ROOT", tmp_path / "not-the-recordings")
    monkeypatch.setattr(settings, "LLM_RECORD_DIR", str(tmp_path))
    from app.services.observability import observation_context

    with observation_context(audit_run_id="run-1"):
        return llm_recording.record(
            prompt=prompt, response_model=Answer, model="m", system_instruction="sys",
            thinking_level=None, max_output_tokens=100, temperature=0.0,
            result=Answer(status="PROVEN"), raw_response='{"status":"PROVEN"}',
        )


def test_recorded_run_replays_exactly_and_seeds_the_response_cache(monkeypatch, tmp_path):
    import replay_recorded_calls as replay
    from app.services import llm_response_cache, pipeline_cache

    key = _record(monkeypatch, tmp_path)
    assert len(key) == 64
    assert key == llm_response_cache.response_key(
        prompt="Does C1 hold?", response_model=Answer, model="m", system_instruction="sys",
        thinking_level=None, max_output_tokens=100,
    )
    index = (tmp_path / "runs" / "run-1.jsonl").read_text(encoding="utf-8").splitlines()
    assert json.loads(index[0])["key"] == key
    records = replay.load_records(tmp_path, "run-1", "all")
    assert replay.check(records)["replay_exact"] is True
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path / "uploads"))
    assert replay.seed_cache(records)["seeded"] == 1
    assert pipeline_cache.get_json(llm_response_cache.NAMESPACE, key) == {"status": "PROVEN", "note": ""}


def test_rerun_scores_decision_agreement_against_the_recording(monkeypatch, tmp_path):
    import replay_recorded_calls as replay

    _record(monkeypatch, tmp_path)
    records = replay.load_records(tmp_path, None, "all")
    monkeypatch.setattr(llm_client, "generate_structured", AsyncMock(return_value=Answer(status="FAILED")))
    report = asyncio.run(replay.rerun(records, model="other", thinking_level=None,
                                      transform=replay.load_transform(None), concurrency=2))
    assert report["decisions"] == 1 and report["decision_agreement"] == 0.0
    assert report["rows"][0]["changes"] == [{"path": "status", "recorded": "PROVEN", "fresh": "FAILED"}]


# ── REL-07 fingerprint ──────────────────────────────────────────────────────

def test_corpus_fingerprint_ignores_generated_figure_text():
    from app.services.pipeline import corpus_source_fingerprint

    def corpus(description, analysis):
        return [{
            "id": "c1", "document_id": "d1",
            "content": "Figure 3 load curve" + (f"\nVISUAL DESCRIPTION: {description}" if description else ""),
            "metadata": {"page": 4, "visual_analysis": analysis},
        }]

    base = corpus_source_fingerprint(corpus(None, {}))
    assert corpus_source_fingerprint(corpus("peak 12 kN", {"model": "a"})) == base
    assert corpus_source_fingerprint(corpus("peak 15 kN", {"model": "b"})) == base
    changed = corpus(None, {})
    changed[0]["content"] = "Figure 3 load curve, revised"
    assert corpus_source_fingerprint(changed) != base


# ── TRU-06 ──────────────────────────────────────────────────────────────────

def test_reviewer_decisions_export_as_cases():
    import export_review_cases as export

    reviewed = {
        "project_id": "p", "req_code": "REQ-1", "title": "t", "description": "d",
        "extracted_parameters": {
            "conditions": [{"id": "C1"}],
            "verification": {"assessed_at": "T1", "audit_run_id": "run-9", "condition_results": [{"condition_id": "C1"}]},
            "review_history": [
                {"id": "e1", "action": "Approved", "resolution_type": "Confirm AI assessment",
                 "ai_verdict": "Supported", "human_verdict": "Supported", "assessment_at": "T0", "reviewer": "Ana"},
                {"id": "e2", "action": "Comment", "resolution_type": "Comment", "comment": "fyi"},
                {"id": "e3", "action": "Rejected", "resolution_type": "Override verdict", "comment": "wrong unit",
                 "ai_verdict": "Supported", "human_verdict": "Conflict", "assessment_at": "T1", "reviewer": "Ana"},
            ],
        },
    }
    stale = {"project_id": "p", "req_code": "REQ-2", "extracted_parameters": {
        "verification": {"assessed_at": "T5", "audit_run_id": "run-10", "condition_results": [{"condition_id": "C9"}]},
        "review_history": [{"id": "e4", "action": "Approved", "resolution_type": "Confirm AI assessment",
                            "ai_verdict": "Partial", "human_verdict": "Partial", "assessment_at": "T4"}],
    }}
    unreviewed = {"project_id": "p", "req_code": "REQ-3", "extracted_parameters": {}}
    cases = export.review_cases([reviewed, stale, unreviewed])
    assert [case["req_code"] for case in cases] == ["REQ-1", "REQ-2"]
    first = cases[0]
    assert first["label"]["expected_status"] == "CONFLICT" and first["label"]["agrees"] is False
    assert first["assessment_matches"] and first["assessment"]["condition_results"] == [{"condition_id": "C1"}]
    assert first["label"]["reviewer_ref"] != "Ana" and first["contract"]["conditions"] == [{"id": "C1"}]
    assert cases[1]["assessment_matches"] is False and cases[1]["assessment"]["condition_results"] is None
    summary = export.summarize(cases)
    assert summary["agreement"] == 0.5 and summary["replayable"] == 1
    assert summary["ai_to_human"] == {"Partial -> Partial": 1, "Supported -> Conflict": 1}
    assert export.output_is_outside_repo(export.REPO / "x.jsonl") is False


# ── ARC-02 settings guard / ARC-05 summary list ─────────────────────────────

def _request(headers):
    from starlette.requests import Request

    return Request({"type": "http", "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()]})


def test_global_ai_settings_need_an_sso_admin_and_no_running_audit(monkeypatch, tmp_path):
    from fastapi import HTTPException
    from app.api import settings as settings_api
    from app.services import audit_progress

    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path))
    monkeypatch.setattr(settings, "TRUST_FORWARDED_IDENTITY", "true", raising=False)
    monkeypatch.setattr(settings, "SETTINGS_ADMINS", ["lead@example.com"])
    with pytest.raises(HTTPException) as refused:
        settings_api.guard_settings_change(_request({"X-Forwarded-User": "u1", "X-Forwarded-Email": "dev@example.com"}))
    assert refused.value.status_code == 403
    settings_api.guard_settings_change(_request({"X-Forwarded-User": "u2", "X-Forwarded-Email": "Lead@example.com"}))
    audit_progress.start("p1", "m")
    with pytest.raises(HTTPException) as busy:
        settings_api.guard_settings_change(_request({"X-Forwarded-User": "u2", "X-Forwarded-Email": "lead@example.com"}))
    assert busy.value.status_code == 409
    monkeypatch.setattr(settings, "TRUST_FORWARDED_IDENTITY", "false", raising=False)
    audit_progress.forget("p1")
    settings_api.guard_settings_change(_request({}))  # local development stays open


def test_requirement_list_summary_view_leaves_out_evidence_and_long_text(tmp_path):
    import httpx
    from fastapi import FastAPI
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from app.database import Base, get_db
    from app.api.requirements import router
    from app.models.project import Project
    from app.models.document import Document, EvidenceChunk
    from app.models.requirement import Requirement, RequirementEvidence

    async def go():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as db:
            db.add(Project(id="p1", name="P", product_name="U", audit_id="A1"))
            db.add(Document(id="d1", project_id="p1", filename="s.pdf", original_filename="s.pdf",
                            storage_path=str(tmp_path / "s.pdf"), page_count=1, processing_status="Indexed"))
            db.add(EvidenceChunk(id="c1", document_id="d1", chunk_index=0, page_number=1, content="REQ-1 opens in 20 ms"))
            db.add(Requirement(id="r1", project_id="p1", req_code="REQ-1", title="Opening", description="long text",
                               ai_analysis="narrative", coverage_status="Supported", extracted_parameters={}))
            db.add(RequirementEvidence(requirement_id="r1", evidence_chunk_id="c1"))
            await db.commit()
        app = FastAPI()
        app.include_router(router, prefix="/api")

        async def session_dependency():
            async with sessions() as db:
                yield db
        app.dependency_overrides[get_db] = session_dependency
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            full = (await client.get("/api/projects/p1/requirements")).json()
            summary = (await client.get("/api/projects/p1/requirements?view=summary")).json()
        await engine.dispose()
        return full, summary

    full, summary = asyncio.run(go())
    assert len(full[0]["evidence"]) == 1 and full[0]["ai_analysis"] == "narrative"
    assert summary[0]["evidence"] == [] and summary[0]["description"] is None and summary[0]["ai_analysis"] is None
    assert summary[0]["coverage_status"] == "Supported" and summary[0]["req_code"] == "REQ-1"


# ── Maverick workspace quota (measured 2026-11-10) ──────────────────────────

def test_burst_goes_through_then_pacing_starts(monkeypatch):
    monkeypatch.setattr(settings, "LLM_REQUESTS_PER_MINUTE", 600)  # one every 100 ms
    monkeypatch.setattr(settings, "LLM_REQUEST_BURST", 3)
    key = request_limits.provider_key("databricks", "burst")

    async def go():
        return [await request_limits.wait_for_provider(key) for _ in range(4)]

    waits = asyncio.run(go())
    assert waits[:3] == [0.0, 0.0, 0.0] and waits[3] > 0.05


def test_workspace_quota_429s_get_their_own_backoff_budget(monkeypatch):
    quota = MagicMock(status_code=429)
    quota.headers = {}
    quota.text = "REQUEST_LIMIT_EXCEEDED"
    _fake_databricks(monkeypatch, [quota, quota, quota, _ok('{"status":"PROVEN"}')])
    sleep = AsyncMock()
    monkeypatch.setattr(llm_client.asyncio, "sleep", sleep)
    diagnostics = {}
    result = asyncio.run(llm_client.call_databricks_chat_completions("p", json_mode=True, diagnostics=diagnostics))
    assert result == '{"status":"PROVEN"}'  # three quota refusals no longer fail the call
    assert diagnostics["rate_limit_retries"] == 3 and diagnostics["attempt_count"] == 4
    delays = [call.args[0] for call in sleep.await_args_list]
    assert delays == sorted(delays) and delays[0] >= 1.0  # backoff grows


def test_persistent_quota_refusal_still_ends_the_call(monkeypatch):
    quota = MagicMock(status_code=429)
    quota.headers = {}
    quota.text = "REQUEST_LIMIT_EXCEEDED"
    client = _fake_databricks(monkeypatch, [quota] * 10)
    monkeypatch.setattr(llm_client.asyncio, "sleep", AsyncMock())
    # One call gives up (the circuit breaker decides when repeated quota failures stop the audit).
    assert asyncio.run(llm_client.call_databricks_chat_completions("p", json_mode=True)) is None
    assert client.post.await_count == llm_client.DATABRICKS_MAX_ATTEMPTS + llm_client.DATABRICKS_RATE_LIMIT_RETRIES
