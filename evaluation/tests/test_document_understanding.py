"""Offline document-understanding suite (UND-04) over the committed Nova parses."""

import json
import re
from pathlib import Path

import pytest

from evaluation import document_understanding as du


# Pinned measurements. A change to ingestion or extraction that moves one of
# these must update it here deliberately.
EXPECTED_TOTALS = {
    "documents": 8,
    "expected": 48,
    # Before UND-01: 24/48 associated (tables 0/16, figures 0/8) and 24
    # blocks containing a neighbour's ID (ELE-003 ran to 2,267 characters).
    "associated": 48,
    "phantom": 0,
    "contaminated": 0,
    "incomplete": 0,
}


@pytest.fixture(scope="module")
def scores():
    return du.run_suite()


def test_every_nova_pdf_has_a_fixture_parse():
    pdfs = sorted(du.DOCUMENT_DIR.glob("*.pdf"))
    assert len(pdfs) == 12
    for pdf in pdfs:
        assert (du.FIXTURE_DIR / f"{du.sha256_file(pdf)}-parse-2.0-figures.json").is_file(), pdf.name


def test_fixture_parses_are_scrubbed():
    forbidden = re.compile(r"/Volumes/|dbfs:|https?://|databricks\.com|azuredatabricks", re.IGNORECASE)
    files = sorted(du.FIXTURE_DIR.glob("*.json"))
    assert len(files) == 12
    for path in files:
        text = path.read_text(encoding="utf-8")
        assert not forbidden.search(text), path.name
        payload = json.loads(text)
        assert payload["parse_version"] == "2.0"
        assert path.name.startswith(payload["source_sha256"])


def test_cache_miss_fails_instead_of_calling_databricks(tmp_path):
    pdf = du.DOCUMENT_DIR / "01_Nova_HV_Electrical_Requirements.pdf"
    from app.services.databricks_document_ai import parse_document

    with du.fixture_parser(cache_dir=tmp_path):
        with pytest.raises(RuntimeError, match="offline"):
            parse_document(str(pdf))


def test_suite_reproduces_pinned_association_numbers(scores):
    summary = du.summarize(scores)
    assert {key: summary[key] for key in EXPECTED_TOTALS} == EXPECTED_TOTALS, summary


def test_no_phantom_requirement_ids(scores):
    assert [item.phantom for item in scores if item.phantom] == []


def test_blocks_stay_small(scores):
    # ELE-003 used to absorb ELE-004..006 (2,267 characters).
    largest = max(chars for item in scores for chars in item.block_chars.values())
    assert largest < 600


def test_table_rows_and_figures_carry_requirement_ids_at_ingestion():
    parsed = du.parse_fixture_document(du.DOCUMENT_DIR / "01_Nova_HV_Electrical_Requirements.pdf")
    tables = [c for c in parsed.chunks if c.metadata["block_type"] == "table"]
    row_ids = [i for c in tables for i in c.metadata["table"]["row_requirement_ids"] if i]
    assert row_ids == ["REQ-NVA-ELE-004", "REQ-NVA-ELE-005"]
    figures = [c for c in parsed.chunks if c.metadata["block_type"] == "figure"]
    assert figures[0].metadata["requirement_ids"] == ["REQ-NVA-ELE-006"]
    text = [c for c in parsed.chunks if c.metadata["block_type"] == "text_section"]
    assert ["REQ-NVA-ELE-001"] in [c.metadata["requirement_ids"] for c in text]


# ── Grammar edge cases for the new block starts ──────────────────────────

from app.services import extraction as ex  # noqa: E402


def test_id_in_a_later_table_column_is_a_trace_not_a_requirement():
    text = (
        "SECTION: Spec > Matrix\nTABLE\n| Test | Traces to | Result |\n| --- | --- | --- |\n"
        "| T-1 | REQ-A-001 | Pass |"
    )
    assert ex._source_blocks_by_code(text) == {}


def test_table_row_block_is_its_header_and_row_only():
    text = (
        "SECTION: Spec > Table\nTABLE\n| Identifier | Text |\n| --- | --- |\n"
        "| REQ-A-001 | The pump shall start. |\n| REQ-A-002 | The valve shall close. |"
        "\n\nSECTION: Spec > Notes\nNotes that are not part of REQ-A-002."
    )
    blocks = ex._source_blocks_by_code(text)
    assert blocks == {
        "REQ-A-001": "| Identifier | Text |\n| REQ-A-001 | The pump shall start. |",
        "REQ-A-002": "| Identifier | Text |\n| REQ-A-002 | The valve shall close. |",
    }
    discovered = ex._targeted_discovery_from_source(blocks, {"REQ-A-002"})
    assert discovered[0].description == "The valve shall close."


def test_figure_labels_count_only_inside_figures_and_never_as_cross_references():
    figure = (
        "SECTION: Spec > Figure\nFIGURE: Figure 1\nInstallation control - REQ-A-006\n"
        "The bus shall fall below 60 V.\nVISUAL DESCRIPTION: arrow towards REQ-A-099"
    )
    assert list(ex._source_blocks_by_code(figure)) == ["REQ-A-006"]
    prose = "SECTION: Spec > Text\nIntro line\nInstallation control - REQ-A-006\nThe bus shall fall below 60 V."
    assert ex._source_blocks_by_code(prose) == {}
    reference = "SECTION: Spec > Figure\nFIGURE: Figure 1\nSee also - REQ-A-006\nA drawing."
    assert ex._source_blocks_by_code(reference) == {}


def test_a_requirement_ends_where_its_section_ends_but_spans_page_breaks():
    text = (
        "SECTION: Spec > REQ-A-001 Pump\nREQ-A-001 The pump shall start\n\n"
        "SECTION: Spec > REQ-A-001 Pump\nwithin 2 s of the command.\n\n"
        "SECTION: Spec > Appendix\nUnrelated appendix text."
    )
    block = ex._source_blocks_by_code(text)["REQ-A-001"]
    assert "within 2 s" in block and "appendix" not in block.lower()


def test_plain_text_without_sections_keeps_next_requirement_boundary():
    text = "REQ-A-001 The pump shall start.\n\nIt shall stop on command.\nREQ-A-002 The valve shall close."
    assert ex._source_blocks_by_code(text)["REQ-A-001"] == (
        "REQ-A-001 The pump shall start.\n\nIt shall stop on command."
    )


def test_rule_fallback_reads_requirement_table_rows():
    reqs = ex.fallback_extract_requirements("| REQ-A-001 | Pump start | The pump shall start within 2 s. |")
    assert [r.req_code for r in reqs] == ["REQ-A-001"]
