"""Cross-document contradiction detection service.

Identifies potential inconsistencies across specifications, supplier datasheets,
test reports, and user manuals for the same requirement.
"""

from typing import Optional
from dataclasses import dataclass
from app.services.verification import extract_numeric_ranges


@dataclass
class ContradictionFinding:
    has_conflict: bool
    source_a_doc: str
    source_a_quote: str
    source_b_doc: str
    source_b_quote: str
    highlight: Optional[str]
    description: str


SEMANTIC_CONFLICT_PAIRS = [
    (
        ["credential", "authentication", "login", "password", "authorized", "restricted"],
        ["no login", "no authentication", "unauthenticated", "no password", "open access", "no login required"],
        ["diagnostic", "port", "service", "access", "uds", "security", "calibration", "flashing", "service port"],
        "Discrepancy in access control / authentication requirements across documentation.",
    ),
    (
        ["isolated", "galvanic isolation", "optical and magnetic isolation"],
        ["non-isolated", "common ground", "shared ground"],
        ["ground", "isolation", "barrier", "chassis", "sensing", "dielectric", "return"],
        "Discrepancy in isolation / grounding architecture between specification and technical documentation.",
    ),
]


def normalize_unit(unit_str: str) -> str:
    if not unit_str:
        return ""
    u = unit_str.strip().lower()
    if "°" in u or "c" in u:
        return "°c"
    if "mv" in u:
        return "mv"
    if "kv" in u:
        return "kv"
    if "v" in u:
        return "v"
    if "ma" in u:
        return "ma"
    if "a" in u:
        return "a"
    if "h" in u:
        return "h"
    if "ms" in u:
        return "ms"
    if "us" in u or "µs" in u:
        return "us"
    if "j" in u:
        return "j"
    return u


def detect_cross_document_contradiction(
    evidence_items: list[dict],
) -> Optional[ContradictionFinding]:
    """Compare evidence chunks from different documents to identify value or semantic contradictions."""
    if len(evidence_items) < 2:
        return None

    # 1. Check numeric ranges from different sources (e.g. 18–32 V in spec vs 18–30 V in supplier datasheet)
    for i in range(len(evidence_items)):
        for j in range(i + 1, len(evidence_items)):
            item_a = evidence_items[i]
            item_b = evidence_items[j]

            # Only compare if from different documents
            if item_a.get("document_name") == item_b.get("document_name"):
                continue

            text_a = item_a.get("quote", "")
            text_b = item_b.get("quote", "")

            ranges_a = extract_numeric_ranges(text_a)
            ranges_b = extract_numeric_ranges(text_b)

            if ranges_a and ranges_b:
                for ra in ranges_a:
                    for rb in ranges_b:
                        ua = normalize_unit(ra.unit)
                        ub = normalize_unit(rb.unit)
                        # Only compare if both have valid, matching physical measurement units
                        if ua and ub and ua == ub:
                            if abs(ra.max_val - rb.max_val) > 0.5:
                                doc_a_lower = item_a.get("document_name", "").lower()
                                doc_b_lower = item_b.get("document_name", "").lower()
                                is_datasheet_or_spec = any(k in doc_a_lower or k in doc_b_lower for k in ["datasheet", "spec", "manual", "srs", "ds-"])
                                if is_datasheet_or_spec:
                                    highlight = f"{rb.max_val:g} {rb.unit}".strip()
                                    desc = (
                                        f"The available evidence indicates a potential discrepancy between {item_a.get('document_name')} and {item_b.get('document_name')}. "
                                        f"One document specifies {ra.raw_str}, while the other specifies {rb.raw_str}."
                                    )
                                    return ContradictionFinding(
                                        has_conflict=True,
                                        source_a_doc=item_a.get("document_name", "Doc A"),
                                        source_a_quote=text_a,
                                        source_b_doc=item_b.get("document_name", "Doc B"),
                                        source_b_quote=text_b,
                                        highlight=highlight,
                                        description=desc,
                                    )

            # 2. Check semantic conflicts (e.g., requires authentication vs no login required)
            for set_a, set_b, topics, explanation in SEMANTIC_CONFLICT_PAIRS:
                # Require topical overlap in both items
                topic_match = any(t in text_a.lower() for t in topics) and any(t in text_b.lower() for t in topics)
                if not topic_match:
                    continue

                a_matches_pos = any(kw in text_a.lower() for kw in set_a)
                b_matches_neg = any(kw in text_b.lower() for kw in set_b)
                if a_matches_pos and b_matches_neg:
                    # Find matching negative keyword for highlight
                    neg_kw = next((kw for kw in set_b if kw in text_b.lower()), None)
                    return ContradictionFinding(
                        has_conflict=True,
                        source_a_doc=item_a.get("document_name", "Doc A"),
                        source_a_quote=text_a,
                        source_b_doc=item_b.get("document_name", "Doc B"),
                        source_b_quote=text_b,
                        highlight=neg_kw,
                        description=f"{explanation} One document describes access requiring credentials while another states '{neg_kw}'.",
                    )
                # Check vice versa
                a_matches_neg = any(kw in text_a.lower() for kw in set_b)
                b_matches_pos = any(kw in text_b.lower() for kw in set_a)
                if a_matches_neg and b_matches_pos:
                    neg_kw = next((kw for kw in set_b if kw in text_a.lower()), None)
                    return ContradictionFinding(
                        has_conflict=True,
                        source_a_doc=item_b.get("document_name", "Doc B"),
                        source_a_quote=text_b,
                        source_b_doc=item_a.get("document_name", "Doc A"),
                        source_b_quote=text_a,
                        highlight=neg_kw,
                        description=f"{explanation} One document describes access requiring credentials while another states '{neg_kw}'.",
                    )

    return None
