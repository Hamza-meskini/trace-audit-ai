"""One requirement-ID grammar shared by ingestion, extraction, retrieval and claims.

Before this module each component carried its own copy of the pattern, so a
change in one place (say, accepting IDs in table cells) silently disagreed
with the others. Everything that recognises or compares a requirement ID
imports it from here.

Two normal forms exist on purpose:

* ``normalize_requirement_code`` is the stored form (upper case, ``_`` -> ``-``,
  CFR-style subparagraph letters kept lower case: ``S7.1(c)``).
* ``comparable_code`` is a lenient comparison key that also folds whitespace
  and runs of separators, used when matching IDs quoted from evidence text.
"""

from __future__ import annotations

import re


# Project-style requirement IDs: REQ-001, REQ-BCU-001, REQ_ELE_001, REQ-NVA-ELE-004.
REQUIREMENT_ID_PATTERN = r"REQ[-_]?[A-Za-z0-9_-]*\d+"

# At the start of a source line, short R-prefixed IDs (R-001, R12) also open
# a requirement. Mid-sentence they are too ambiguous to count as a mention.
LEADING_ID_PATTERN = rf"(?:{REQUIREMENT_ID_PATTERN}|R[-_]?[A-Za-z0-9_-]*\d+)"

# Hierarchical regulatory clauses (FMVSS style): S5, S7.6.6, S7.1(c).
REGULATORY_CODE_PATTERN = r"S\d+(?:\.\d+)*(?:\([a-z0-9]+\))*"

# A requirement ID mentioned anywhere in text.
REQ_CODE_RE = re.compile(rf"\b({REQUIREMENT_ID_PATTERN})\b", re.IGNORECASE)

# Dotted clause identifiers such as ``FMVSS305-S5.3(a)`` or ``ISO26262-6.4``.
CLAUSE_CODE_RE = re.compile(
    r"\b([A-Za-z][A-Za-z0-9_-]*\d+(?:\.\d+)+(?:\([A-Za-z0-9]+\))*)\b",
    re.IGNORECASE,
)

# A line that opens a requirement with its ID (``REQ-001: ...``).
LEADING_ID_RE = re.compile(rf"^[ \t]*({LEADING_ID_PATTERN})", re.IGNORECASE)


def normalize_requirement_code(value: str | None) -> str:
    """The stored form of a requirement ID."""
    code = (value or "").strip().upper().replace("_", "-")
    return re.sub(r"\(([A-Z0-9]+)\)", lambda item: f"({item.group(1).lower()})", code)


def comparable_code(value: str | None) -> str:
    """A lenient key for comparing IDs quoted in evidence (``REQ_ELE 001`` == ``REQ-ELE-001``)."""
    return re.sub(r"[-_\s]+", "-", (value or "").strip().upper())


def id_match_key(value: str | None) -> str:
    """Key under which two spellings of one ID are equal.

    Folds case, separators (``REQ_NVA_ELE_003`` == ``REQ-NVA-ELE-003``) and
    leading zeros in numeric parts (``ELE-3`` == ``ELE-003``).
    """
    key = comparable_code(value)
    return re.sub(r"(?<![0-9])0+(?=[0-9])", "", key)


_RANGE_RE = re.compile(
    rf"\b({REQUIREMENT_ID_PATTERN})\s*(?:to|through|thru|until|–|—|\.\.)\s*({REQUIREMENT_ID_PATTERN})\b",
    re.IGNORECASE,
)
_MAX_RANGE = 500


def _expand_range(first: str, last: str) -> list[str]:
    head_a = re.match(r"^(.*?)(\d+)$", comparable_code(first))
    head_b = re.match(r"^(.*?)(\d+)$", comparable_code(last))
    if not head_a or not head_b or head_a.group(1) != head_b.group(1):
        return []
    low, high = int(head_a.group(2)), int(head_b.group(2))
    if high < low or high - low > _MAX_RANGE:
        return []
    width = len(head_a.group(2))
    return [f"{head_a.group(1)}{number:0{width}d}" for number in range(low, high + 1)]


def requirement_id_keys(text: str | None) -> set[str]:
    """Match keys of every requirement ID named in ``text``, ranges expanded.

    ``REQ-NVA-ELE-001 to REQ-NVA-ELE-005`` names all five requirements.
    """
    keys = {id_match_key(code) for code in REQ_CODE_RE.findall(text or "")}
    for first, last in _RANGE_RE.findall(text or ""):
        keys.update(id_match_key(code) for code in _expand_range(first, last))
    return keys


def mentioned_requirement_codes(text: str | None) -> list[str]:
    """Every project-style requirement ID mentioned in ``text``, normalized, in order."""
    seen: dict[str, None] = {}
    for match in REQ_CODE_RE.findall(text or ""):
        seen.setdefault(normalize_requirement_code(match), None)
    return list(seen)
