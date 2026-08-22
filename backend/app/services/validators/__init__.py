"""Deterministic audit validators package."""

from dataclasses import dataclass
from typing import Optional, Literal


ValidationStatus = Literal["SUPPORTED", "PARTIAL", "CONFLICT", "MISSING", "UNKNOWN"]


@dataclass
class ValidationOutcome:
    status: ValidationStatus
    confidence: float
    reason: str
    highlight: Optional[str] = None
    expected_value: Optional[str] = None
    observed_value: Optional[str] = None
