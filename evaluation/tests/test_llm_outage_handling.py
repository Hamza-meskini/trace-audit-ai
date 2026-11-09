"""REL-01 / REL-02 / EVA-02: an unavailable model stops work instead of degrading it.

The recorded body is the HTTP 400 every Foundation Model call returned during
the 2026-11-08 outage.
"""
import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from app.config import settings
from app.services import audit_worker, llm_client, pipeline
from app.services import classification, document_classifier
from app.services.llm_client import LLM_BREAKER, LLMUnavailableError
from evaluation import run_validity

OUTAGE_BODY = ('{"error_code":"BAD_REQUEST","message":"BAD_REQUEST: Cannot create or query foundation '
               'model endpoints, please try again later."}')
MAVERICK = "system.ai.llama-4-maverick"


class Answer(BaseModel):
    value: str


@pytest.fixture
def endpoint(monkeypatch):
    """A fake Databricks endpoint; set .status/.body, read .calls."""
    monkeypatch.setattr(settings, "DATABRICKS_TOKEN", "test")
    monkeypatch.setattr(settings, "DATABRICKS_BASE_URL", "https://example.test/v1")
    monkeypatch.setattr(settings, "LLM_DATABRICKS_ONLY", True)
    monkeypatch.setattr(settings, "LLM_BREAKER_THRESHOLD", 3)
    monkeypatch.setattr(settings, "LLM_BREAKER_COOLDOWN_SECONDS", 60.0)
    monkeypatch.setattr(llm_client, "_databricks_retry_delay", lambda *_: 0.0)
    state = SimpleNamespace(status=400, body=OUTAGE_BODY, calls=0)

    async def post(url, json=None, headers=None):
        state.calls += 1
        response = MagicMock(status_code=state.status, text=state.body, headers={})
        response.json.return_value = {"choices": [{"message": {"content": '{"value": "ok"}'}}],
                                      "model": MAVERICK}
        return response

    client = MagicMock()
    client.post = post
    context = AsyncMock()
    context.__aenter__.return_value = client
    monkeypatch.setattr(llm_client.httpx, "AsyncClient", lambda **kwargs: context)
    return state


def call():
    return asyncio.run(llm_client.generate_structured("p", Answer, model=MAVERICK))


def test_outage_opens_the_breaker_after_three_calls_then_stops_calling(endpoint):
    assert call() is None
    assert call() is None
    with pytest.raises(LLMUnavailableError) as raised:
        call()
    assert raised.value.kind == "outage"
    assert endpoint.calls == 3
    for _ in range(5):
        with pytest.raises(LLMUnavailableError):
            call()
    assert endpoint.calls == 3  # no further network traffic while open
    with pytest.raises(LLMUnavailableError):
        llm_client.raise_if_llm_unavailable()
    assert "kept its previous result" in raised.value.user_message


def test_rejected_token_opens_the_breaker_at_once(endpoint):
    endpoint.status, endpoint.body = 403, '{"message": "Provided access token does not have required scopes"}'
    with pytest.raises(LLMUnavailableError) as raised:
        call()
    assert raised.value.kind == "auth"
    assert endpoint.calls == 1


def test_a_bad_request_is_not_an_outage(endpoint):
    endpoint.body = '{"message": "max_tokens must be positive"}'
    for _ in range(5):
        assert call() is None
    llm_client.raise_if_llm_unavailable()  # still closed


def test_success_resets_the_count(endpoint):
    call(); call()
    endpoint.status = 200
    assert call().value == "ok"
    endpoint.status = 400
    call(); call()
    llm_client.raise_if_llm_unavailable()  # two failures since the success


def test_after_cooldown_one_probe_is_allowed_and_success_closes(endpoint, monkeypatch):
    for _ in range(2):
        call()
    with pytest.raises(LLMUnavailableError):
        call()
    monkeypatch.setattr(settings, "LLM_BREAKER_COOLDOWN_SECONDS", 0.0)
    endpoint.status = 200
    assert call().value == "ok"
    llm_client.raise_if_llm_unavailable()


def test_failure_classification():
    assert llm_client.classify_llm_failure(400, OUTAGE_BODY) == "outage"
    assert llm_client.classify_llm_failure(404, "'x' does not exist") == "outage"
    assert llm_client.classify_llm_failure(401) == "auth"
    assert llm_client.classify_llm_failure(429) == "quota"
    assert llm_client.classify_llm_failure(503) == "transient"
    assert llm_client.classify_llm_failure(None, "ConnectError") == "transient"
    assert llm_client.classify_llm_failure(400, "invalid schema") is None


def test_profiling_raises_instead_of_falling_back_to_heuristics(endpoint, monkeypatch):
    monkeypatch.setattr(settings, "LLM_BREAKER_THRESHOLD", 1)
    with pytest.raises(LLMUnavailableError):
        asyncio.run(document_classifier.classify_documents_with_llm([{"filename": "a.pdf", "excerpt": "x"}]))


def test_cached_heuristic_profile_is_redone_when_a_model_is_configured(endpoint):
    assert document_classifier._reprofile_heuristic({"classification_source": "content_heuristic"})
    assert not document_classifier._reprofile_heuristic({"classification_source": "llm_content_profile"})


def test_production_verification_has_no_rule_engine_fallback(endpoint, monkeypatch):
    seen = {}

    async def fake_verify(**kwargs):
        seen.update(kwargs)
        raise RuntimeError("stop here")

    import app.services.verification_reasoner as vr
    monkeypatch.setattr(vr, "evaluate_requirement_verification", fake_verify)
    monkeypatch.setattr(classification, "_deterministic_prechecks", lambda *a, **k: (([], [], []), None))
    with pytest.raises(RuntimeError, match="stop here"):
        asyncio.run(classification.assess_requirement_coverage_async(
            req_code="REQ-1", title="t", description="The unit shall open within 20 ms.",
            category="General", candidate_chunks=[], model=MAVERICK))
    assert seen["allow_deterministic_fallback"] is False


def test_failed_verification_is_recognised_and_not_saved():
    failed = SimpleNamespace(pipeline_diagnostics={"decision_source": "llm_error"})
    ok = SimpleNamespace(pipeline_diagnostics={"decision_source": "llm"})
    assert pipeline._is_llm_failure(failed)
    assert pipeline._is_llm_failure(SimpleNamespace(pipeline_diagnostics={"decision_source": "operational_failure"}))
    assert not pipeline._is_llm_failure(ok)


def test_worker_reports_an_honest_outage_message(monkeypatch):
    updates = []
    monkeypatch.setattr(audit_worker.audit_progress, "update", lambda *a, **k: updates.append((a, k)))
    monkeypatch.setattr(audit_worker, "_set_project_status", AsyncMock())

    class Ctx:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(audit_worker, "get_db_context", lambda: Ctx())
    monkeypatch.setattr(audit_worker, "run_audit_pipeline",
                        AsyncMock(side_effect=LLMUnavailableError("outage", MAVERICK, "HTTP 400: " + OUTAGE_BODY)))
    asyncio.run(audit_worker._execute({"project_id": "p1", "run_id": "r1"}))
    args, kwargs = updates[-1]
    assert kwargs["status"] == "failed"
    assert kwargs["error_kind"] == "llm_outage"
    assert "not serving requests" in kwargs["error"]
    audit_worker._set_project_status.assert_awaited_with("p1", "Analysis stopped: model unavailable")


def test_benchmark_run_is_invalid_when_any_call_failed():
    assessments = {
        "REQ-1": SimpleNamespace(pipeline_diagnostics={"decision_source": "llm"}),
        "REQ-2": SimpleNamespace(pipeline_diagnostics={"decision_source": "operational_failure"}),
    }
    profiles = {"spec": SimpleNamespace(classification_source="content_heuristic")}
    with pytest.raises(run_validity.RunInvalidError, match="REQ-2.*operational_failure"):
        run_validity.assert_run_valid(assessments, profiles)
    assert len(run_validity.invalid_reasons(assessments, profiles)) == 2
    run_validity.assert_run_valid({"REQ-1": assessments["REQ-1"]})


def test_benchmark_preflight_aborts_on_an_empty_reply(monkeypatch):
    monkeypatch.setattr(run_validity, "generate_text", AsyncMock(return_value=None))
    with pytest.raises(run_validity.RunInvalidError, match="Preflight"):
        asyncio.run(run_validity.preflight_models([MAVERICK, MAVERICK]))
    assert run_validity.generate_text.await_count == 1


def test_failed_model_call_reaches_the_pipeline_as_a_failure(endpoint, monkeypatch):
    """The seam: a None from the model on the production path must be flagged, not decided."""
    import app.services.verification_reasoner as vr

    monkeypatch.setattr(vr, "generate_structured", AsyncMock(return_value=None))
    monkeypatch.setattr(classification, "_deterministic_prechecks", lambda *a, **k: (([], [], []), None))
    result = asyncio.run(classification.assess_requirement_coverage_async(
        req_code="REQ-1", title="Opening time", description="The unit shall open within 20 ms.",
        category="General", candidate_chunks=[], model=MAVERICK,
        conditions=[{"condition_id": "C1", "description": "Opening time within 20 ms",
                     "parameter": "opening time", "operator": "<=", "value": 20, "unit": "ms",
                     "mandatory": True}],
    ))
    assert result.pipeline_diagnostics.get("decision_source") == "llm_error"
    assert pipeline._is_llm_failure(result)
