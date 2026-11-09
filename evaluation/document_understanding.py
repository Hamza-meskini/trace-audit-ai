"""Offline document-understanding suite built from cached Databricks parses.

The Nova benchmark PDFs were parsed once with ``ai_parse_document``; those
parses are committed (Volume paths scrubbed) under
``evaluation/fixtures/nova_parses`` with the exact cache file name and schema
that ``databricks_document_ai._load_cached_parse`` accepts. This module drives
the production ingestion path (``parse_document`` -> structure-aware chunks)
against that fixture cache with every remote call disabled, then measures how
well extraction can associate each ground-truth requirement with its source.

Run ``python -B evaluation/document_understanding.py`` for a summary. Nothing
here calls an LLM or Databricks: a cache miss raises instead of parsing.

Measured per specification document:

* associated  - ground-truth IDs that extraction maps to a source block. A
  requirement without an association is flagged "source association is
  unresolved" and can never auto-close.
* phantom     - IDs extraction would associate that are not in ground truth.
* contaminated - associated blocks that also contain another requirement's ID
  (a block that has absorbed its neighbours).

The pipeline also appends vision-recovered page text before extraction
(``requirement_visual_recovery``). For Nova the figure obligation is already in
the parse content, so association is measurable without that live stage.
"""

from __future__ import annotations

import contextlib
import hashlib
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

REPO = Path(__file__).resolve().parent.parent
if str(REPO / "backend") not in sys.path:
    sys.path.insert(0, str(REPO / "backend"))
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

FIXTURE_DIR = REPO / "evaluation" / "fixtures" / "nova_parses"
DOCUMENT_DIR = REPO / "evaluation" / "extraction_benchmark" / "documents"


def _offline(*_args: Any, **_kwargs: Any) -> Any:
    raise RuntimeError("document-understanding suite is offline: the fixture cache missed")


@contextlib.contextmanager
def fixture_parser(cache_dir: Path = FIXTURE_DIR) -> Iterator[None]:
    """Point the Databricks parser at the fixture cache and forbid remote calls."""
    from app.config import settings
    from app.services import databricks_document_ai as dda

    saved_settings = (
        settings.DATABRICKS_DOCUMENT_CACHE_DIR,
        settings.DATABRICKS_AI_PREP_SEARCH_ENABLED,
    )
    saved_calls = (dda._run_remote_parse, dda._run_remote_prep_search)
    settings.DATABRICKS_DOCUMENT_CACHE_DIR = str(cache_dir)
    settings.DATABRICKS_AI_PREP_SEARCH_ENABLED = False
    dda._run_remote_parse = _offline
    dda._run_remote_prep_search = _offline
    try:
        yield
    finally:
        settings.DATABRICKS_DOCUMENT_CACHE_DIR, settings.DATABRICKS_AI_PREP_SEARCH_ENABLED = saved_settings
        dda._run_remote_parse, dda._run_remote_prep_search = saved_calls


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def parse_fixture_document(pdf: Path):
    """Parse one benchmark PDF through the production path, from the fixture cache."""
    from app.services.databricks_document_ai import parse_document

    with fixture_parser():
        return parse_document(str(pdf))


def document_text(parsed) -> str:
    """The extraction input, joined exactly as ``pipeline.py`` joins chunks."""
    return "\n\n".join(chunk.content for chunk in parsed.chunks)


def benchmark_documents() -> list[dict[str, Any]]:
    from evaluation.extraction_benchmark.benchmark_data import DOCUMENTS

    return DOCUMENTS


def ground_truth_codes(document: dict[str, Any]) -> list[str]:
    return [item["requirement_id"] for item in document.get("requirements") or []]


def source_associations(text: str) -> dict[str, str]:
    """Requirement ID -> source block, per extraction section, as staged extraction sees it."""
    from app.services import extraction

    associations: dict[str, str] = {}
    # Mirrors extract_requirements_from_text: whole-document blocks win.
    document_sources = extraction._source_blocks_by_code(text)
    for section in extraction._split_text_into_chunks(text):
        source_by_code = extraction._source_blocks_by_code(section)
        for code in source_by_code:
            if code in document_sources:
                source_by_code[code] = document_sources[code]
        for code in source_by_code:
            requirement = extraction.DiscoveredRequirement(
                req_code=code, title=code, description=code, category="General", severity="Medium",
            )
            associations.setdefault(
                code, extraction._discovered_source_block(requirement, source_by_code, section),
            )
    return associations


@dataclass
class DocumentScore:
    filename: str
    expected: list[str]
    associated: list[str] = field(default_factory=list)
    phantom: list[str] = field(default_factory=list)
    contaminated: dict[str, list[str]] = field(default_factory=dict)
    incomplete: list[str] = field(default_factory=list)
    block_chars: dict[str, int] = field(default_factory=dict)

    @property
    def missing(self) -> list[str]:
        return [code for code in self.expected if code not in self.associated]


def _flat(value: str) -> str:
    return " ".join((value or "").split()).lower()


def score_document(document: dict[str, Any], text: str) -> DocumentScore:
    from app.services.extraction import _normalize_requirement_code

    expected = ground_truth_codes(document)
    associations = source_associations(text)
    score = DocumentScore(filename=document["filename"], expected=expected)
    score.associated = [code for code in expected if code in associations]
    for requirement in document.get("requirements") or []:
        block = associations.get(requirement["requirement_id"])
        if block is not None and _flat(requirement["requirement_text"]) not in _flat(block):
            score.incomplete.append(requirement["requirement_id"])
    score.phantom = sorted(code for code in associations if code not in expected)
    for code, block in associations.items():
        score.block_chars[code] = len(block)
        upper = block.upper()
        others = [
            other for other in expected
            if other != code and _normalize_requirement_code(other) in upper
        ]
        if others:
            score.contaminated[code] = others
    return score


def run_suite() -> list[DocumentScore]:
    scores = []
    for document in benchmark_documents():
        if not document.get("requirements"):
            continue
        parsed = parse_fixture_document(DOCUMENT_DIR / document["filename"])
        scores.append(score_document(document, document_text(parsed)))
    return scores


def summarize(scores: list[DocumentScore]) -> dict[str, Any]:
    return {
        "documents": len(scores),
        "expected": sum(len(item.expected) for item in scores),
        "associated": sum(len(item.associated) for item in scores),
        "phantom": sum(len(item.phantom) for item in scores),
        "contaminated": sum(len(item.contaminated) for item in scores),
        "incomplete": sum(len(item.incomplete) for item in scores),
        "missing": [code for item in scores for code in item.missing],
    }


def main() -> int:
    scores = run_suite()
    for item in scores:
        print(
            f"{item.filename}: associated {len(item.associated)}/{len(item.expected)}"
            f" missing={item.missing} phantom={item.phantom}"
            f" contaminated={sorted(item.contaminated)} incomplete={item.incomplete}"
        )
        if "-v" in sys.argv:
            for code, chars in item.block_chars.items():
                print(f"    {code}: {chars} chars")
    summary = summarize(scores)
    print(
        f"TOTAL associated {summary['associated']}/{summary['expected']}"
        f"  phantom {summary['phantom']}  contaminated blocks {summary['contaminated']}"
        f"  incomplete blocks {summary['incomplete']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
