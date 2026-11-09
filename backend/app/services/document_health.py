"""Per-document health signals that must stop automatic closure.

Two signals live here, both computed offline from what ingestion already has:

* Revision families (UND-03). When two uploaded documents are likely
  revisions of one another, both are retrieved as evidence and the value a
  verdict rests on can flip between audits (a probe flipped between 20 ms and
  15 ms). Until a person says which revision is current, evidence from either
  one cannot auto-close a requirement. A family is resolved when all but one
  member are profiled as SUPERSEDED.
* Parser fallback (UND-09). When the configured layout parser failed and a
  local fallback produced the chunks, tables and figures may be degraded.

Each signal becomes a ``document_review`` reason on that document's chunks.
``classification._finalize_assessment`` turns any such reason on the
evidence it was shown into a review requirement. Documents without a reason
are untouched, so saved runs replay identically.
"""

from __future__ import annotations

import re
from pathlib import PurePath
from typing import Any, Iterable


# Word 5-shingle Jaccard similarity at or above which two different files are
# treated as revisions of one document. The twelve Nova documents, built from
# one template, score at most 0.18 against each other; a copy with one value
# changed scores 0.98.
REVISION_SIMILARITY_THRESHOLD = 0.6
_SHINGLE = 5

_REVISION_TOKEN_RE = re.compile(
    r"\b(?:rev(?:ision)?[\s._-]*[a-z0-9]{1,3}|r\d{1,3}|v\d+(?:[._]\d+)*|"
    r"\d{4}[-_.]?\d{2}[-_.]?\d{2}|copy|final|draft|updated|new|old|latest)\b"
    r"|\(\d+\)",
    re.IGNORECASE,
)


def revision_name_key(filename: str) -> str:
    """A file name with revision, date and copy markers removed."""
    stem = PurePath(filename or "").stem.lower()
    stem = re.sub(r"[_]+", " ", stem)
    stem = _REVISION_TOKEN_RE.sub(" ", stem)
    return re.sub(r"[^a-z0-9]+", " ", stem).strip()


def _shingles(text: str) -> set[tuple[str, ...]]:
    lines = [
        line for line in (text or "").splitlines()
        if not line.startswith(("SECTION:", "VISUAL DESCRIPTION:"))
    ]
    words = re.findall(r"[a-z0-9]+(?:\.[0-9]+)?", " ".join(lines).lower())
    if len(words) < _SHINGLE:
        return {tuple(words)} if words else set()
    return {tuple(words[index:index + _SHINGLE]) for index in range(len(words) - _SHINGLE + 1)}


def content_similarity(left: str, right: str) -> float:
    a, b = _shingles(left), _shingles(right)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def revision_families(documents: Iterable[dict[str, Any]]) -> list[list[str]]:
    """Group documents that are likely revisions of one another.

    Each document is ``{"id", "name", "text", "sha256"?}``. Byte-identical
    uploads are not revisions (they cannot disagree). Two documents are linked
    when their names match after removing revision markers, or when their text
    is near-identical. Returns families of two or more document IDs.
    """
    items = [item for item in documents if item.get("id")]
    parent = {item["id"]: item["id"] for item in items}

    def find(node: str) -> str:
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    shingles = {item["id"]: _shingles(item.get("text") or "") for item in items}
    for index, left in enumerate(items):
        for right in items[index + 1:]:
            if left.get("sha256") and left.get("sha256") == right.get("sha256"):
                continue
            same_name = (
                revision_name_key(left.get("name", "")) != ""
                and revision_name_key(left.get("name", "")) == revision_name_key(right.get("name", ""))
            )
            a, b = shingles[left["id"]], shingles[right["id"]]
            similar = bool(a and b) and len(a & b) / len(a | b) >= REVISION_SIMILARITY_THRESHOLD
            if same_name or similar:
                parent[find(left["id"])] = find(right["id"])

    groups: dict[str, list[str]] = {}
    for item in items:
        groups.setdefault(find(item["id"]), []).append(item["id"])
    return [members for members in groups.values() if len(members) > 1]


def revision_review_reasons(
    documents: list[dict[str, Any]],
    statuses: dict[str, str] | None = None,
) -> dict[str, str]:
    """Document ID -> review reason for every member of an unresolved revision family.

    ``statuses`` maps document IDs to their profiled status; a family whose
    members are all SUPERSEDED but one is resolved, and only the superseded
    members keep a reason (they must not serve as evidence at all).
    """
    names = {item["id"]: item.get("name") or item["id"] for item in documents}
    statuses = statuses or {}
    reasons: dict[str, str] = {}
    for family in revision_families(documents):
        current = [doc_id for doc_id in family if statuses.get(doc_id) != "SUPERSEDED"]
        listed = ", ".join(sorted(names[doc_id] for doc_id in family))
        if len(current) == 1:
            for doc_id in family:
                if doc_id not in current:
                    reasons[doc_id] = (
                        f"{names[doc_id]} is a superseded revision of {names[current[0]]}; "
                        "it must not be used as evidence."
                    )
            continue
        for doc_id in family:
            reasons[doc_id] = (
                f"{names[doc_id]} looks like another revision of the same document ({listed}); "
                "choose the current revision before this evidence can close a requirement."
            )
    return reasons


def apply_document_review(
    chunks: list[dict[str, Any]],
    documents: list[dict[str, Any]],
    statuses: dict[str, str] | None = None,
) -> dict[str, list[str]]:
    """Attach ``document_review`` reasons to the retrieval chunks of affected documents.

    ``chunks`` are pipeline retrieval dicts (``document_id``, ``content``,
    ``metadata``); ``documents`` are ``{"id", "name"}``. Returns the reasons
    per document ID for the run diagnostics.
    """
    texts: dict[str, list[str]] = {}
    shas: dict[str, str] = {}
    diagnostics: dict[str, dict[str, Any]] = {}
    for chunk in chunks:
        doc_id = chunk.get("document_id")
        if not doc_id:
            continue
        texts.setdefault(doc_id, []).append(chunk.get("content") or "")
        metadata = chunk.get("metadata") or {}
        if metadata.get("source_sha256"):
            shas.setdefault(doc_id, metadata["source_sha256"])
        if isinstance(metadata.get("document_diagnostics"), dict):
            diagnostics.setdefault(doc_id, metadata["document_diagnostics"])
    corpus = [
        {
            "id": item["id"],
            "name": item.get("name"),
            "text": "\n\n".join(texts.get(item["id"], [])),
            "sha256": shas.get(item["id"]),
        }
        for item in documents
        if item.get("id") in texts
    ]
    reasons: dict[str, list[str]] = {}
    for doc_id, reason in revision_review_reasons(corpus, statuses).items():
        reasons.setdefault(doc_id, []).append(reason)
    for doc_id, doc_diagnostics in diagnostics.items():
        reason = parser_fallback_reason(doc_diagnostics)
        if reason:
            reasons.setdefault(doc_id, []).append(reason)
    for chunk in chunks:
        doc_reasons = reasons.get(chunk.get("document_id"))
        if doc_reasons:
            chunk["metadata"] = {**(chunk.get("metadata") or {}), "document_review": list(doc_reasons)}
    return reasons


def parser_fallback_reason(diagnostics: dict[str, Any] | None) -> str | None:
    """A review reason when a document's layout parse came from a fallback parser."""
    diagnostics = diagnostics or {}
    fallback = diagnostics.get("parser_fallback")
    if not fallback:
        return None
    requested = fallback.get("requested") if isinstance(fallback, dict) else None
    used = diagnostics.get("parser_backend") or "a local parser"
    detail = fallback.get("error") if isinstance(fallback, dict) else None
    return (
        f"The {requested or 'configured'} layout parser failed and {used} produced this document's "
        "text; tables and figures may be degraded"
        + (f" ({detail})" if detail else "")
        + "."
    )
