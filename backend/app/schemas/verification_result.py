"""Pydantic Schema for Structured Multi-Condition Verification Reasoning."""

from typing import Literal, Optional
from pydantic import BaseModel, Field


class RequirementCondition(BaseModel):
    """A distinct mandatory technical condition extracted from a requirement."""
    condition: str
    parameter: Optional[str] = None
    required_value: Optional[str] = None
    unit: Optional[str] = None
    is_mandatory: bool = True


class EvidenceFinding(BaseModel):
    """An explicit finding observed from a specific evidence excerpt."""
    evidence_id: Optional[str] = None
    document_name: Optional[str] = None
    document_type: Optional[Literal["test_report", "datasheet", "specification", "compliance_matrix", "architecture_spec", "other"]] = "other"
    finding: str
    supports_condition: bool
    is_partial_or_pending: bool = False
    is_contradiction: bool = False
    quote: str


class VerificationAnalysisResult(BaseModel):
    """Structured output from multi-condition compliance evaluation."""
    status: Literal["SUPPORTED", "PARTIAL", "MISSING", "UNKNOWN", "CONFLICT"]
    confidence: int = Field(default=85, ge=0, le=100)
    requirement_conditions: list[RequirementCondition] = Field(default_factory=list)
    evidence_findings: list[EvidenceFinding] = Field(default_factory=list)
    reason: str
    highlight: Optional[str] = None


class BatchVerificationItemResult(BaseModel):
    """Evaluation result for a single requirement within a batch."""
    req_code: str
    status: Literal["SUPPORTED", "PARTIAL", "MISSING", "UNKNOWN", "CONFLICT"]
    confidence: int = Field(default=85, ge=0, le=100)
    reason: str
    highlight: Optional[str] = None


class BatchVerificationResult(BaseModel):
    """Batch verification payload from Gemini LLM."""
    batch_results: list[BatchVerificationItemResult] = Field(default_factory=list)

