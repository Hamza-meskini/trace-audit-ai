"""EVA-07: the one scorer every runner, the replay and compare_runs share.

An auto-close is a requirement the review gate closed without a human
("Reviewed" from the machine). Every wrong auto-close is unsafe: a wrong
SUPPORTED hides a gap, and a wrong CONFLICT files a false finding nobody
checked. Earlier runners counted only the first kind.
"""
from __future__ import annotations

from typing import Iterable

FINAL_CLASSES = ["SUPPORTED", "PARTIAL", "CONFLICT", "MISSING", "UNKNOWN", "NOT_APPLICABLE"]
AUTO_CLOSED_STATE = "Reviewed"


def macro_f1(pairs: Iterable[tuple[str, str]], classes: Iterable[str] = FINAL_CLASSES) -> float:
    """Macro-F1 over the gold classes that have support, in percent."""
    pairs = list(pairs)
    scores = []
    for label in classes:
        support = sum(1 for gold, _ in pairs if gold == label)
        if not support:
            continue
        tp = sum(1 for gold, pred in pairs if gold == label and pred == label)
        fp = sum(1 for gold, pred in pairs if gold != label and pred == label)
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / support
        scores.append(2 * precision * recall / (precision + recall) if precision + recall else 0.0)
    return round(100.0 * sum(scores) / len(scores), 2) if scores else 0.0


def is_auto_closed(review_state: str | None) -> bool:
    return str(review_state or "").strip().lower() == AUTO_CLOSED_STATE.lower()


def auto_close_safety(rows: Iterable[tuple[str, str, str | None]]) -> dict:
    """Score (gold, predicted, review_state) triples for auto-close safety."""
    auto = [(gold, pred) for gold, pred, review in rows if is_auto_closed(review)]
    wrong_supported = sum(1 for gold, pred in auto if pred == "SUPPORTED" and gold != "SUPPORTED")
    wrong_conflict = sum(1 for gold, pred in auto if pred == "CONFLICT" and gold != "CONFLICT")
    wrong_other = sum(1 for gold, pred in auto if gold != pred and pred not in ("SUPPORTED", "CONFLICT"))
    return {
        "auto_closed": len(auto),
        "auto_closed_correct": sum(1 for gold, pred in auto if gold == pred),
        "unsafe_supported": wrong_supported,
        "unsafe_conflict": wrong_conflict,
        # Every wrong auto-close, whatever its label.
        "unsafe_auto_closes": wrong_supported + wrong_conflict + wrong_other,
    }
