"""Revision families (UND-03) and document-health review reasons."""

import pytest

from app.services import document_health as dh
from evaluation import document_understanding as du


REPORT = "09_Nova_Electrical_Thermal_Verification_Report.pdf"


@pytest.fixture(scope="module")
def nova_corpus():
    corpus = []
    for pdf in sorted(du.DOCUMENT_DIR.glob("*.pdf")):
        parsed = du.parse_fixture_document(pdf)
        corpus.append({
            "id": pdf.name,
            "name": pdf.name,
            "text": du.document_text(parsed),
            "sha256": parsed.source_sha256,
            "chunks": parsed.chunks,
        })
    return corpus


def revised_copy(document, old="20 ms", new="15 ms", name="09_Nova_Electrical_Thermal_Verification_Report_revB.pdf"):
    assert old in document["text"]
    return {
        "id": name,
        "name": name,
        "text": document["text"].replace(old, new, 1),
        "sha256": "revised",
    }


def test_distinct_nova_documents_are_not_revisions(nova_corpus):
    assert dh.revision_families(nova_corpus) == []


def test_a_one_value_revision_is_detected_by_content(nova_corpus):
    report = next(item for item in nova_corpus if item["id"] == REPORT)
    revision = revised_copy(report, name="Electrical verification (issued).pdf")
    families = dh.revision_families([*nova_corpus, revision])
    assert families == [[REPORT, revision["id"]]]


def test_revision_markers_in_file_names_link_documents():
    assert dh.revision_name_key("Pump_Spec_RevB.pdf") == dh.revision_name_key("pump spec rev C (1).pdf")
    assert dh.revision_name_key("Pump_Spec_v2.1_2026-01-04.pdf") == "pump spec"
    families = dh.revision_families([
        {"id": "a", "name": "Pump_Spec_RevA.pdf", "text": "alpha beta gamma delta epsilon zeta", "sha256": "1"},
        {"id": "b", "name": "Pump_Spec_RevB.pdf", "text": "one two three four five six seven", "sha256": "2"},
        {"id": "c", "name": "Valve_Spec_RevA.pdf", "text": "one two three four five six seven", "sha256": "3"},
    ])
    # b and c share content, a and b share a name: one family of three.
    assert sorted(families[0]) == ["a", "b", "c"]


def test_byte_identical_uploads_are_not_revisions():
    text = "the pump shall start within two seconds of the command"
    assert dh.revision_families([
        {"id": "a", "name": "x.pdf", "text": text, "sha256": "same"},
        {"id": "b", "name": "y.pdf", "text": text, "sha256": "same"},
    ]) == []


def test_unresolved_family_flags_both_members_and_superseded_resolves_it(nova_corpus):
    report = next(item for item in nova_corpus if item["id"] == REPORT)
    revision = revised_copy(report)
    corpus = [report, revision]
    reasons = dh.revision_review_reasons(corpus)
    assert set(reasons) == {REPORT, revision["id"]}
    assert "choose the current revision" in reasons[REPORT]

    resolved = dh.revision_review_reasons(corpus, {REPORT: "SUPERSEDED", revision["id"]: "FINAL"})
    assert list(resolved) == [REPORT]
    assert "superseded revision" in resolved[REPORT]


def test_apply_document_review_marks_every_chunk_of_flagged_documents(nova_corpus):
    report = next(item for item in nova_corpus if item["id"] == REPORT)
    other = next(item for item in nova_corpus if item["id"] != REPORT)
    chunks = []
    for doc_id, source, replace in ((REPORT, report, False), ("rev", report, True), (other["id"], other, False)):
        for chunk in source["chunks"]:
            content = chunk.content.replace("20 ms", "15 ms") if replace else chunk.content
            metadata = dict(chunk.metadata)
            if replace:
                metadata["source_sha256"] = "revised"
            chunks.append({"document_id": doc_id, "content": content, "metadata": metadata})
    reasons = dh.apply_document_review(
        chunks, [{"id": REPORT, "name": REPORT}, {"id": "rev", "name": "rev.pdf"}, {"id": other["id"], "name": other["name"]}],
    )
    assert set(reasons) == {REPORT, "rev"}
    flagged = {c["document_id"] for c in chunks if c["metadata"].get("document_review")}
    assert flagged == {REPORT, "rev"}


def test_forced_parser_fallback_is_recorded_and_becomes_a_review_reason(monkeypatch):
    from app.services import databricks_document_ai, ingestion

    def fail(_path):
        raise RuntimeError("warehouse unavailable")

    monkeypatch.setattr(databricks_document_ai, "parse_document", fail)
    pdf = du.DOCUMENT_DIR / "01_Nova_HV_Electrical_Requirements.pdf"
    parsed = ingestion.parse_pdf_document(str(pdf), backend="databricks-auto")
    fallback = parsed.diagnostics["parser_fallback"]
    assert fallback == {"requested": "databricks", "error": "RuntimeError: warehouse unavailable"}
    assert parsed.chunks[0].metadata["document_diagnostics"]["parser_fallback"] == fallback

    chunks = [
        {"document_id": "d1", "content": chunk.content, "metadata": dict(chunk.metadata)}
        for chunk in parsed.chunks
    ]
    reasons = dh.apply_document_review(chunks, [{"id": "d1", "name": pdf.name}])
    assert "databricks layout parser failed" in reasons["d1"][0]
    assert all(chunk["metadata"]["document_review"] for chunk in chunks)


def test_a_healthy_parse_has_no_fallback_reason():
    parsed = du.parse_fixture_document(du.DOCUMENT_DIR / "01_Nova_HV_Electrical_Requirements.pdf")
    assert "parser_fallback" not in parsed.diagnostics
    assert dh.parser_fallback_reason(parsed.diagnostics) is None


def _supported_assessment(evidence_items):
    from app.schemas.contract import parse_requirement_contract
    from app.services.classification import _finalize_assessment
    from app.services.validators import ValidationOutcome

    contract = parse_requirement_contract(
        req_code="REQ-NVA-ELE-004", title="Contactor opening",
        description="The contactors shall open within 20 ms.", category="Electrical",
    )
    return _finalize_assessment(
        contract, evidence_items, ValidationOutcome(status="SUPPORTED", confidence=99.0, reason="ok"),
        pipeline_diagnostics={"decision_source": "test"},
    )


def test_flagged_evidence_blocks_auto_close_and_unflagged_evidence_is_unchanged():
    from app.services.classification import _format_evidence_items

    chunk = {"id": "c1", "document_name": "r.pdf", "content": "Opening measured at 15 ms.", "metadata": {}}
    plain = _supported_assessment(_format_evidence_items([chunk]))
    assert not any("Evidence document needs review" in r for r in plain.pipeline_diagnostics["review_gate"]["reasons"])
    assert "document_review" not in _format_evidence_items([chunk])[0]

    chunk["metadata"] = {"document_review": ["r.pdf looks like another revision"]}
    flagged = _supported_assessment(_format_evidence_items([chunk]))
    gate = flagged.pipeline_diagnostics["review_gate"]
    assert "Evidence document needs review: r.pdf looks like another revision" in gate["reasons"]
    assert gate["auto_close_eligible"] is False
    assert flagged.review_state == "Needs review"
