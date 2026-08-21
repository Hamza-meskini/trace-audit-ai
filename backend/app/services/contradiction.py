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
        "Discrepancy in access control / authentication requirements across documentation.",
    ),
    (
        ["isolated", "galvanic isolation"],
        ["non-isolated", "common ground", "shared ground"],
        "Discrepancy in isolation / grounding architecture between specification and technical documentation.",
    ),
]


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
                ra = ranges_a[0]
                rb = ranges_b[0]
                # If units match or are compatible and upper limits conflict (e.g. 32 V vs 30 V)
                if abs(ra.max_val - rb.max_val) > 0.5:
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
            for set_a, set_b, explanation in SEMANTIC_CONFLICT_PAIRS:
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
