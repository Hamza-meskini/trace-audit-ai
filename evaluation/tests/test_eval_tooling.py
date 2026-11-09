"""EVA-01/04/07/08: replay fixtures, production-path runner, shared scorer, comparable arms."""
import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "evaluation"))

from evaluation import compare_runs  # noqa: E402
from evaluation.scoring import auto_close_safety, macro_f1  # noqa: E402

FIXTURE_RUNS = (
    "20260915T140120.543292Z", "20260915T222056.820191Z", "20260916T133251.054900Z",
    "20260922T115355.064325Z", "20260922T123239.778672Z",
)


def test_replay_fixtures_are_committed():
    for run_id in FIXTURE_RUNS:
        assert (REPO / "evaluation" / "fixtures" / "replay" / f"{run_id}.results.json.gz").exists()


def test_every_wrong_auto_close_is_unsafe():
    rows = [
        ("SUPPORTED", "SUPPORTED", "Reviewed"),
        ("PARTIAL", "SUPPORTED", "Reviewed"),   # wrong SUPPORTED
        ("PARTIAL", "CONFLICT", "Reviewed"),    # wrong CONFLICT, previously uncounted
        ("PARTIAL", "CONFLICT", "Needs review"),  # not auto-closed
    ]
    safety = auto_close_safety(rows)
    assert safety == {"auto_closed": 3, "auto_closed_correct": 1, "unsafe_supported": 1,
                      "unsafe_conflict": 1, "unsafe_auto_closes": 2}


def test_macro_f1_matches_hand_computation():
    assert macro_f1([("SUPPORTED", "SUPPORTED"), ("PARTIAL", "PARTIAL")]) == 100.0
    assert macro_f1([("SUPPORTED", "PARTIAL"), ("PARTIAL", "PARTIAL")]) == pytest.approx(33.33, abs=0.01)


def run(model="m", parser="docling", path="batch", commit="a"):
    return {"benchmark_id": "nova", "benchmark_version": "1", "mode": "end-to-end", "model": model,
            "atomic_model": model, "verification_model": model, "verification_path": path,
            "generation_config": {"provider": "databricks", "temperature": 0, "batch_size": 4,
                                  "retrieval_backend": "local",
                                  "environment": {"document_parser": parser, "git_commit": commit}},
            "requirements": []}


def test_arms_that_differ_only_in_code_are_comparable():
    assert compare_runs.settings_mismatches({"a": [run(commit="a")], "b": [run(commit="b")]}, set()) == []


def test_arms_with_different_settings_are_refused():
    problems = compare_runs.settings_mismatches({"a": [run()], "b": [run(parser="pymupdf")]}, set())
    assert problems and "document_parser" in problems[0]


def test_an_intended_difference_is_allowed():
    arms = {"a": [run(path="batch")], "b": [run(path="production")]}
    assert compare_runs.settings_mismatches(arms, {"verification_path"}) == []


def test_runs_within_an_arm_must_match():
    problems = compare_runs.settings_mismatches({"a": [run(), run(model="other")]}, {"model"})
    assert problems and "run 2" in problems[0]


def test_production_path_runs_single_requirement_verification(monkeypatch):
    import evaluation.run_nova_end_to_end_benchmark as nova

    seen = []

    async def assess(**kwargs):
        seen.append(kwargs)
        return SimpleNamespace(review_state="Reviewed", pipeline_diagnostics={})

    monkeypatch.setattr(nova, "assess_requirement_coverage_async", assess)
    items = [{"req_code": "REQ-1", "description": "d"}, {"req_code": "REQ-2", "description": "d"}]
    result = asyncio.run(nova.production_path_assessments(
        items, model="m", thinking_level=None, spec_doc_names=set(), concurrency=2))
    assert set(result) == {"REQ-1", "REQ-2"}
    assert all(call["defer_partial"] and call["defer_secondary_adjudication"] for call in seen)
