"""TRU-02: who closed a requirement, read from its review history.

The pipeline gives SUPPORTED and CONFLICT verdicts the review state "Reviewed"
without a person looking at them. Reports must not count those as human reviews,
and a later comment must not be exported as the reviewer's decision.
"""
from __future__ import annotations

from typing import Any

HUMAN_DECISION_STATES = ("Approved", "Rejected", "Reviewed", "Needs review")
HUMAN_CLOSED_STATES = ("Approved", "Rejected", "Reviewed")
AUTO_CLOSED = "Auto-closed"


def latest_human_decision(history: list[dict[str, Any]] | None, review_state: str | None) -> dict | None:
    """The reviewer event that set the current review state, if a person set it.

    Comments are skipped; a later system re-audit event means no human decision
    stands any more.
    """
    for event in reversed(history or []):
        if event.get("system"):
            return None
        if event.get("action") == "Comment":
            continue
        if event.get("action") == review_state and review_state in HUMAN_DECISION_STATES:
            return event
        return None
    return None


def comments_after(history: list[dict[str, Any]] | None, decision: dict | None) -> list[dict]:
    """Human comments recorded after the decision (all of them when there is none)."""
    events = list(history or [])
    start = 0
    if decision is not None:
        for index, event in enumerate(events):
            if event is decision or (decision.get("id") and event.get("id") == decision.get("id")):
                start = index + 1
    return [e for e in events[start:] if e.get("action") == "Comment" and not e.get("system")]


def review_origin(history: list[dict[str, Any]] | None, review_state: str | None) -> str:
    """'human', 'auto' (machine closed), 're-audit' (system reopened) or 'open'."""
    events = history or []
    if latest_human_decision(events, review_state) is not None:
        return "human"
    last = next((e for e in reversed(events) if e.get("action") != "Comment"), None)
    if last is not None and last.get("system"):
        return "re-audit"
    if review_state == "Reviewed":
        return "auto"
    return "open"


def review_label(history: list[dict[str, Any]] | None, review_state: str | None) -> str:
    state = review_state or "Open"
    return AUTO_CLOSED if review_origin(history, state) == "auto" else state


def is_human_reviewed(history: list[dict[str, Any]] | None, review_state: str | None) -> bool:
    return review_state in HUMAN_CLOSED_STATES and review_origin(history, review_state) == "human"
