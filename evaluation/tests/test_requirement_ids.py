"""One requirement-ID grammar (UND-02)."""

import asyncio
import json
import re
from pathlib import Path

from app.schemas import claim
from app.services import extraction, requirement_ids, retrieval, verdict_aggregator
from evaluation.extraction_benchmark.benchmark_data import DOCUMENTS


REPO = Path(__file__).resolve().parents[2]

# The patterns and normalizers each component carried before the shared module.
OLD_REQ_RE = re.compile(r"\b(REQ[-_]?[A-Za-z0-9_-]*\d+)\b", re.IGNORECASE)


def old_extraction_normalize(value):
    code = (value or "").strip().upper().replace("_", "-")
    return re.sub(r"\(([A-Z0-9]+)\)", lambda item: f"({item.group(1).lower()})", code)


def old_aggregator_norm(code):
    return re.sub(r"[-_\s]+", "-", (code or "").strip().upper())


def known_codes():
    codes = {item["requirement_id"] for doc in DOCUMENTS for item in doc.get("requirements") or []}
    ledger = json.loads((REPO / "evaluation" / "replay_expectations.json").read_text(encoding="utf-8"))
    codes |= {item["requirement_id"] for item in ledger["changes"]}
    return sorted(codes | {"REQ_ELE_001", "req-bcu-7", "S7.1(C)", "S12.2(b)(2)", "R-12"})


def test_every_component_uses_the_shared_patterns():
    assert retrieval.REQ_CODE_REGEX is requirement_ids.REQ_CODE_RE
    assert retrieval.CLAUSE_CODE_REGEX is requirement_ids.CLAUSE_CODE_RE
    assert claim.REQ_CODE_REGEX is requirement_ids.REQ_CODE_RE
    assert verdict_aggregator._REQ_CODE_RE is requirement_ids.REQ_CODE_RE
    assert verdict_aggregator._norm_code is requirement_ids.comparable_code


def test_shared_normalizers_agree_with_the_ones_they_replace():
    for code in known_codes():
        assert requirement_ids.normalize_requirement_code(code) == old_extraction_normalize(code)
        assert extraction._normalize_requirement_code(code) == old_extraction_normalize(code)
        assert requirement_ids.comparable_code(code) == old_aggregator_norm(code)
        assert requirement_ids.REQ_CODE_RE.findall(f"x {code} y") == OLD_REQ_RE.findall(f"x {code} y")


def test_mentioned_codes_are_normalized_and_ordered():
    text = "See REQ_ELE_002 and REQ-ELE-001, then req-ele-002 again."
    assert requirement_ids.mentioned_requirement_codes(text) == ["REQ-ELE-002", "REQ-ELE-001"]


def test_duplicate_project_ids_are_reported_and_the_normative_block_wins():
    text = (
        "SECTION: Spec > Overview\nREQ-A-001 Pump overview and background.\n\n"
        "SECTION: Spec > Pump\nREQ-A-001 The pump shall start within 2 s."
    )
    duplicates = {}
    blocks = extraction._source_blocks_by_code(text, duplicates=duplicates)
    assert blocks["REQ-A-001"] == "REQ-A-001 The pump shall start within 2 s."
    assert list(duplicates) == ["REQ-A-001"]


def test_repeated_identical_blocks_are_not_duplicates():
    text = "REQ-A-001 The pump shall start.\nREQ-A-001 The pump shall start."
    duplicates = {}
    extraction._source_blocks_by_code(text, duplicates=duplicates)
    assert duplicates == {}


def test_staged_extraction_flags_a_duplicated_id(monkeypatch):
    text = (
        "SECTION: Spec > Overview\nREQ-A-001 Pump overview and background.\n\n"
        "SECTION: Spec > Pump\nREQ-A-001 The pump shall start within 2 s."
    )

    async def construct(item, plan, source, **_kwargs):
        return extraction.ExtractedRequirement(
            req_code=item.req_code, title=item.title, description=item.description,
            category="General", severity="Medium", contract_complete=True,
        )

    monkeypatch.setattr(extraction, "_construct_atomic_contract", construct)
    result = asyncio.run(extraction._extract_chunk_staged(
        text, doc_name="spec", active_model="m", thinking_level=None,
        chunk_label="s", allow_rule_fallback=True, allow_model_fallback=False,
        target_req_codes={"REQ-A-001"}, construction_mode="direct",
    ))
    assert result[0].contract_complete is False
    assert any("defined by more than one source block" in issue for issue in result[0].validation_issues)


def test_nova_documents_have_no_duplicate_ids():
    from evaluation import document_understanding as du

    for document in DOCUMENTS:
        if not document.get("requirements"):
            continue
        parsed = du.parse_fixture_document(du.DOCUMENT_DIR / document["filename"])
        duplicates = {}
        extraction._source_blocks_by_code(du.document_text(parsed), duplicates=duplicates)
        assert duplicates == {}, document["filename"]
