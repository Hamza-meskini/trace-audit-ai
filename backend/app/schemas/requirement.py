"""Pydantic schemas for Requirements."""

from datetime import datetime
from pydantic import BaseModel, Field


class EvidenceResponse(BaseModel):
    id: str
    document_name: str
    page_number: int | None
    quote: str
    status: str
    label: str
    highlight: str | None = None


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

    model_config = {"from_attributes": True}


class RequirementCreate(BaseModel):
    req_code: str = Field(..., min_length=1, max_length=50)
    title: str = Field(..., min_length=1)
    description: str | None = None
    category: str = "Uncategorized"
    source_document: str | None = None
    severity: str = "Medium"
