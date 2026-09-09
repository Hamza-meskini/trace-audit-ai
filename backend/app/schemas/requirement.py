"""Pydantic schemas for Requirements."""

from datetime import datetime
from typing import Any, Literal
from pydantic import BaseModel, Field


class EvidenceResponse(BaseModel):
    id: str
    document_name: str
    page_number: int | None
    quote: str
    status: str
    label: str
    highlight: str | None = None
    document_id: str | None = None
    chunk_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class RequirementResponse(BaseModel):
    id: str
    project_id: str
    req_code: str
    title: str
    description: str | None
    category: str
    source_document: str | None
    sources_count: int
    coverage_status: str
    confidence: float
    review_state: str
    severity: str
    ai_analysis: str | None
    ai_recommendation: str | None
    evidence: list[EvidenceResponse] = []
    created_at: datetime
    updated_at: datetime
    contract: dict[str, Any] = Field(default_factory=dict)
    condition_results: list[dict[str, Any]] = Field(default_factory=list)
    diagnostics: dict[str, Any] = Field(default_factory=dict)
    source_document_id: str | None = None
    source_blocks: list[dict[str, Any]] = Field(default_factory=list)
    review_history: list[dict[str, Any]] = Field(default_factory=list)

    model_config = {"from_attributes": True}


class RequirementCreate(BaseModel):
    req_code: str = Field(..., min_length=1, max_length=50)
    title: str = Field(..., min_length=1)
    description: str | None = None
    category: str = "Uncategorized"
    source_document: str | None = None
    severity: str = "Medium"


class RequirementReviewRequest(BaseModel):
    action: Literal["Approved", "Rejected", "Reviewed", "Needs review", "Comment"]
    reviewer: str = Field(min_length=1, max_length=120)
    comment: str = Field(min_length=1, max_length=5000)
