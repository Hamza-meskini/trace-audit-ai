"""Deterministic audit validators package."""

from dataclasses import dataclass, field
from typing import Any, Optional, Literal


ValidationStatus = Literal[
    "SUPPORTED",
    "PARTIAL",
    "CONFLICT",
    "MISSING",
    "UNKNOWN",
    "NOT_APPLICABLE",
]


@dataclass
class ValidationOutcome:
    status: ValidationStatus
    confidence: float
    reason: str
    highlight: Optional[str] = None
    expected_value: Optional[str] = None
    observed_value: Optional[str] = None
    condition_results: list[Any] = field(default_factory=list)
