"""Batch 4 levers, all offline: TRU-03, ACC-04, ACC-05, ACC-11, REL-03, REL-04, REL-08."""
import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_accuracy_levers import leaf, make_contract  # noqa: E402

from app.config import settings
from app.schemas.evidence_qualification import parameters_compatible
from app.schemas.verification_result import ConditionVerificationResult
from app.services import audit_worker, model_readiness, verdict_aggregator as va
from app.services import verification_reasoner as vr
from app.services.audit_progress import AuditCancelled
from app.services.evidence_qualification import evidence_attributed_to_requirement
from app.services.llm_client import LLMUnavailableError


def cr(cid, status, **kw):
    return ConditionVerificationResult(condition_id=cid, status=status, **kw)


# ── TRU-03: monotone validation state ────────────────────────────────────────

def test_valid_cannot_erase_a_raised_marker():
    result = cr("C1", "PENDING")
    result.mark_validation("UNRESOLVED")
    result.mark_validation("VALID")
    assert result.validation_state == "UNRESOLVED"
    result.mark_validation("CONTRADICTED")
    result.mark_validation("UNRESOLVED")
    assert result.validation_state == "CONTRADICTED"


def test_valid_refines_the_unconfirmed_default():
    result = cr("C1", "PROVEN")
    assert result.validation_state == "UNRESOLVED" and result.validation_floor is None
    result.mark_validation("VALID")
    assert result.validation_state == "VALID"


def test_insufficient_numeric_fields_is_a_marker_for_pending():
    contract = make_contract(ids=("C1",))
    contract.atomic_conditions[0].operator = "<="
    contract.atomic_conditions[0].threshold = 5.0
    analysis = SimpleNamespace(condition_results=[cr("C1", "PENDING", execution_state="PARTIALLY_EXECUTED",
                                                      relationship="PARTIAL_COVERAGE",
                                                      evidence_value_role="OBSERVED")])
    vr._audit_llm_condition_metadata(contract, analysis)
    result = analysis.condition_results[0]
    assert result.validation_floor == "UNRESOLVED"
    result.mark_validation("VALID")  # what a later qualification pass writes
    assert result.validation_state == "UNRESOLVED"


def test_unresolved_condition_that_cannot_move_the_aggregate_keeps_the_atomic_verdict():
    contract = make_contract(ids=("C1", "C2", "C3"))
    results = [cr("C1", "INCONCLUSIVE"), cr("C2", "INCONCLUSIVE"), cr("C3", "PENDING")]
    issue = SimpleNamespace(code="EVIDENCE_VALIDATION", path="C3")
    status, _, _ = va.aggregate_condition_statuses(contract, results)
    assert va.atomic_status_is_insensitive(contract, results, [issue], status) is (
        all(va.aggregate_condition_statuses(contract, [results[0], results[1], cr("C3", s)])[0] == status
            for s in ("PROVEN", "FAILED", "PENDING", "UNTESTED", "INCONCLUSIVE"))
    )
    decisive = [cr("C1", "PROVEN"), cr("C2", "PROVEN"), cr("C3", "PENDING")]
    status, _, _ = va.aggregate_condition_statuses(contract, decisive)
    assert not va.atomic_status_is_insensitive(contract, decisive, [issue], status)


# ── ACC-04 (L3a): nested IF_THEN matches the flat rule ──────────────────────

def test_nested_if_then_untested_antecedent_with_inconclusive_consequent_is_unknown():
    tree = {"operator": "IF_THEN", "antecedent": leaf("C1"),
            "consequent": {"operator": "ALL_OF", "children": [leaf("C2"), leaf("C3")]}}
    contract = make_contract(tree=tree, ids=("C1", "C2", "C3"))
    by_id = {r.condition_id: r for r in (cr("C1", "UNTESTED"), cr("C2", "INCONCLUSIVE"), cr("C3", "INCONCLUSIVE"))}
    assert va._aggregate_logic_tree_node(contract.logic_tree, by_id) == "UNKNOWN"
    by_id["C2"] = cr("C2", "UNTESTED")
    by_id["C3"] = cr("C3", "UNTESTED")
    assert va._aggregate_logic_tree_node(contract.logic_tree, by_id) == "MISSING"


# ── ACC-05 (L1c): ID-attributed qualification ───────────────────────────────

@pytest.mark.parametrize("text, expected", [
    ("REQ-NVA-THM-002 | Thermal soak | Executed | PASS", True),
    ("req_nva_thm_002 executed at 85 C", True),
    ("Limits are defined in REQ-NVA-THM-002.", False),
    ("see REQ-NVA-THM-002", False),
    ("REQ-NVA-THM-0021 | other", False),
    ("REQ-NVA-THM-003 | Thermal soak", False),
])
def test_requirement_id_attribution(text, expected):
    assert evidence_attributed_to_requirement("REQ-NVA-THM-002", text) is expected


# ── ACC-11: whole-word aliases and id-keyed pairing ─────────────────────────

def test_alias_matches_whole_words_only():
    assert parameters_compatible("current", None, "Pyro trigger PASS on bench rig") is None
    assert parameters_compatible("current", None, "quiescent current 142 uA") is True


def test_qualifications_pair_with_chunks_by_evidence_id():
    chunks = [{"content": "spec"}, {"content": "lab"}, {"content": "matrix"}]
    quals = [SimpleNamespace(evidence_id="E2"), SimpleNamespace(evidence_id="E3")]  # E1 was a skipped spec chunk
    assert [c["content"] for _, c in vr._quals_with_chunks(quals, chunks)] == ["lab", "matrix"]


# ── REL-04: no repair for a legitimate all-untested first pass ───────────────

def test_all_untested_first_pass_triggers_no_logic_repair():
    tree = {"operator": "IF_THEN", "antecedent": leaf("C1"), "consequent": leaf("C2")}
    contract = make_contract(tree=tree)
    assert vr._logic_retry_condition_ids(contract, [cr("C1", "UNTESTED"), cr("C2", "UNTESTED")]) == []
    assert vr._logic_retry_condition_ids(contract, [cr("C1", "INCONCLUSIVE"), cr("C2", "UNTESTED")]) == ["C1"]


def test_satisfied_any_of_triggers_no_logic_repair():
    tree = {"operator": "ANY_OF", "children": [leaf("C1"), leaf("C2")]}
    contract = make_contract(tree=tree)
    assert vr._logic_retry_condition_ids(contract, [cr("C1", "PROVEN"), cr("C2", "UNTESTED")]) == []


# ── REL-03: readiness probe ─────────────────────────────────────────────────

@pytest.fixture
def readiness(monkeypatch):
    monkeypatch.setattr(settings, "AUDIT_READINESS_CHECK", True)
    monkeypatch.setattr(model_readiness, "llm_credentials_ready", lambda: True)
    model_readiness.reset()
    return monkeypatch


def test_empty_reply_means_not_ready_and_is_cached_briefly(readiness):
    probe = AsyncMock(return_value=None)
    readiness.setattr(model_readiness.llm_client, "generate_text", probe)
    reason = asyncio.run(model_readiness.not_ready_reason("system.ai.llama-4-maverick"))
    assert reason and "not started" in reason
    asyncio.run(model_readiness.not_ready_reason("system.ai.llama-4-maverick"))
    assert probe.await_count == len(model_readiness.role_models("system.ai.llama-4-maverick"))


def test_outage_error_is_reported_honestly(readiness):
    readiness.setattr(model_readiness.llm_client, "generate_text",
                      AsyncMock(side_effect=LLMUnavailableError("outage", "m", "HTTP 400")))
    assert "not serving" in asyncio.run(model_readiness.not_ready_reason("m"))


def test_ready_models_pass(readiness):
    readiness.setattr(model_readiness.llm_client, "generate_text", AsyncMock(return_value="OK"))
    assert asyncio.run(model_readiness.not_ready_reason(None)) is None


def test_readiness_is_off_without_credentials(monkeypatch):
    monkeypatch.setattr(settings, "AUDIT_READINESS_CHECK", True)
    monkeypatch.setattr(model_readiness, "llm_credentials_ready", lambda: False)
    assert asyncio.run(model_readiness.not_ready_reason(None)) is None


# ── REL-08: cancel stops in-flight work ─────────────────────────────────────

def test_cancel_aborts_in_flight_work_within_a_poll(monkeypatch):
    monkeypatch.setattr(audit_worker, "CANCEL_POLL_SECONDS", 0.01)
    flags = {"cancel": False, "aborted": False}
    monkeypatch.setattr(audit_worker.audit_progress, "is_cancel_requested", lambda _pid: flags["cancel"])

    async def slow_pipeline():
        try:
            await asyncio.sleep(30)  # an in-flight model call
        except asyncio.CancelledError:
            flags["aborted"] = True
            raise

    async def scenario():
        task = asyncio.create_task(audit_worker._run_cancellable("p1", slow_pipeline))
        await asyncio.sleep(0.05)
        flags["cancel"] = True
        with pytest.raises(AuditCancelled):
            await asyncio.wait_for(task, timeout=2)

    asyncio.run(scenario())
    assert flags["aborted"]


def test_completed_work_returns_its_result(monkeypatch):
    monkeypatch.setattr(audit_worker.audit_progress, "is_cancel_requested", lambda _pid: False)

    async def quick():
        return {"ok": True}

    assert asyncio.run(audit_worker._run_cancellable("p1", quick)) == {"ok": True}


# ── TRU-05: only cited evidence is labelled, per condition ──────────────────

def _item(chunk_id, text):
    return {"chunk_id": chunk_id, "document_name": f"{chunk_id}.pdf", "doc_type": "Test Report",
            "page_number": 1, "quote": text}


def test_uncited_chunks_are_context_and_cited_ones_carry_their_span():
    from app.services.classification import CONTEXT_LINK_STATUS, _cited_evidence_links

    items = [_item("a", "Background: the unit is described here."),
             _item("b", "Run 3: opening time measured 18 ms, PASS."),
             _item("c", "Run 4: leakage 12 uA exceeded the 10 uA limit.")]
    results = [cr("C1", "PROVEN", quote="opening time measured 18 ms"),
               cr("C2", "FAILED", quote="leakage 12 uA exceeded the 10 uA limit")]
    links = {link.chunk_id: link for link in _cited_evidence_links(items, results)}
    assert links["a"].status == CONTEXT_LINK_STATUS and links["a"].highlight is None
    assert links["b"].status == "Supports requirement" and links["b"].cited_conditions == ["C1"]
    assert links["b"].highlight == "opening time measured 18 ms"
    assert links["c"].status == "Potential conflict" and links["c"].cited_conditions == ["C2"]
    ordered = [link.chunk_id for link in _cited_evidence_links(items, results)]
    assert ordered[-1] == "a"  # context after cited evidence


def test_a_supported_verdict_does_not_turn_every_chunk_into_support():
    from app.services.classification import CONTEXT_LINK_STATUS, _cited_evidence_links

    links = _cited_evidence_links([_item("a", "Unrelated retrieved text about packaging.")],
                                  [cr("C1", "PROVEN", quote="opening time measured 18 ms")])
    assert links[0].status == CONTEXT_LINK_STATUS


# ── REL-07: requeues are capped ──────────────────────────────────────────────

def test_a_run_that_keeps_dying_is_not_requeued_forever(monkeypatch, tmp_path):
    from app.services import audit_progress

    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path))
    audit_progress.start("p1", "m")
    for expected_attempt in range(1, audit_progress.MAX_REQUEUE_ATTEMPTS + 1):
        job = audit_progress.claim_next("w")
        assert job["attempt"] == expected_attempt
        audit_progress.recover_interrupted(requeue=True)  # the server died mid-run
    final = audit_progress.latest("p1")
    assert final["status"] == "interrupted"
    assert "will not be resumed" in final["error"]


# ── ARC-01: answers are reused, logic re-runs ───────────────────────────────

def test_repeat_call_is_served_from_the_answer_cache(monkeypatch, tmp_path):
    from pydantic import BaseModel
    from app.services import llm_response_cache

    class Answer(BaseModel):
        value: str

    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path))
    monkeypatch.setattr(settings, "LLM_RESPONSE_CACHE_ENABLED", True)
    calls = AsyncMock(return_value=Answer(value="ok"))
    monkeypatch.setattr(llm_response_cache.llm_client, "generate_structured", calls)

    first = asyncio.run(llm_response_cache.generate_structured("p", Answer, model="m"))
    second = asyncio.run(llm_response_cache.generate_structured("p", Answer, model="m"))
    assert first == second == Answer(value="ok")
    assert calls.await_count == 1  # the second (re-audit) call is free
    asyncio.run(llm_response_cache.generate_structured("changed prompt", Answer, model="m"))
    assert calls.await_count == 2


def test_failed_answers_are_never_cached(monkeypatch, tmp_path):
    from pydantic import BaseModel
    from app.services import llm_response_cache

    class Answer(BaseModel):
        value: str

    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path))
    monkeypatch.setattr(settings, "LLM_RESPONSE_CACHE_ENABLED", True)
    calls = AsyncMock(side_effect=[None, Answer(value="ok")])
    monkeypatch.setattr(llm_response_cache.llm_client, "generate_structured", calls)
    assert asyncio.run(llm_response_cache.generate_structured("p", Answer, model="m")) is None
    assert asyncio.run(llm_response_cache.generate_structured("p", Answer, model="m")) == Answer(value="ok")
    assert calls.await_count == 2


def test_verification_reasoner_uses_the_answer_cache():
    from app.services import llm_response_cache

    assert vr.generate_structured is llm_response_cache.generate_structured


# ── ARC-02: effective configuration snapshot ────────────────────────────────

def test_effective_config_hides_secrets_and_is_stable(monkeypatch):
    from app.services.effective_config import effective_config

    monkeypatch.setattr(settings, "DATABRICKS_TOKEN", "dapi0123456789abcdef0123")
    first = effective_config()
    assert first["settings"]["DATABRICKS_TOKEN"] == "<set>"
    assert "dapi0123456789abcdef0123" not in str(first)
    assert first["settings"]["LLM_MODEL"] == settings.LLM_MODEL
    assert effective_config()["sha256"] == first["sha256"]
    monkeypatch.setattr(settings, "LLM_BREAKER_THRESHOLD", 99)
    assert effective_config()["sha256"] != first["sha256"]


# ── ACC-12: tolerances are differences ──────────────────────────────────────

def test_temperature_differences_convert_without_offset():
    from app.services.units import convert_difference, convert_value, is_difference_parameter

    assert convert_value(40, "degF", "degC") == pytest.approx(4.444, abs=1e-3)  # absolute level
    assert convert_difference(40, "degF", "degC") == pytest.approx(22.222, abs=1e-3)  # tolerance
    assert convert_difference(5, "K", "degC") == pytest.approx(5.0)
    assert convert_difference(2, "mV", "V") == pytest.approx(0.002)
    assert is_difference_parameter("temperature tolerance") and not is_difference_parameter("temperature")


def test_sibling_tolerance_in_another_unit_is_converted():
    from app.schemas.claim import EvidenceClaim
    from app.schemas.contract import AtomicConditionContract

    contract = make_contract(ids=("C1", "C2"))
    contract.atomic_conditions = [
        AtomicConditionContract(condition_id="C1", description="setpoint", parameter="setpoint temperature",
                                operator="==", threshold=100.0, unit="degC"),
        AtomicConditionContract(condition_id="C2", description="tolerance", parameter="temperature tolerance",
                                operator="<=", threshold=9.0, unit="degF"),  # = 5 K
    ]
    within = EvidenceClaim(document_name="r.pdf", quote="q", claim_type="threshold", value=104.0, unit="degC")
    outside = EvidenceClaim(document_name="r.pdf", quote="q", claim_type="threshold", value=106.0, unit="degC")
    cond = contract.atomic_conditions[0]
    assert va._numeric_condition_status(contract, cond, within) == "PROVEN"
    assert va._numeric_condition_status(contract, cond, outside) in ("FAILED", None)


# ── ACC-07 / ACC-08 ─────────────────────────────────────────────────────────

def test_unconfigured_adjudicator_is_reported(monkeypatch):
    monkeypatch.setattr(settings, "SECONDARY_ADJUDICATOR_ENABLED", True)
    monkeypatch.setattr(settings, "SECONDARY_ADJUDICATOR_MODEL", "")
    monkeypatch.setattr(settings, "DATABRICKS_FALLBACK_MODELS", [])
    assert "never runs" in vr.adjudicator_configuration_warning()
    monkeypatch.setattr(settings, "SECONDARY_ADJUDICATOR_MODEL", "system.ai.other-model")
    assert vr.adjudicator_configuration_warning() is None


def test_observation_disputes_use_the_corrected_trigger_table():
    partial_only = cr("C1", "INCONCLUSIVE", execution_state="PARTIALLY_EXECUTED", relationship="UNCLEAR")
    partial_cover = cr("C2", "INCONCLUSIVE", execution_state="PARTIALLY_EXECUTED", relationship="PARTIAL_COVERAGE")
    consistent = cr("C3", "PROVEN", execution_state="EXECUTED", relationship="SATISFIES", evidence_value_role="OBSERVED")
    assert va.observation_status(partial_only) is None  # partial execution alone derives nothing
    assert va.observation_disputes([partial_only, partial_cover, consistent]) == [
        {"condition_id": "C2", "status": "INCONCLUSIVE", "observed": "PENDING"}
    ]
