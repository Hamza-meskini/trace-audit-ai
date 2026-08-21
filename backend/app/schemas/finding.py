"""Pydantic schemas for Findings."""

from datetime import datetime
from pydantic import BaseModel, Field


class FindingResponse(BaseModel):
    id: str
    project_id: str
    requirement_id: str | None
    finding_code: str
    finding_type: str
    severity: str
    review_state: str
    assigned_to: str | None
    description: str | None
    sources_count: int
    category: str
    requirement_title: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class FindingUpdate(BaseModel):
    review_state: str | None = None
    assigned_to: str | None = None
    severity: str | None = None
