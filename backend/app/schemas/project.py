"""Pydantic schemas for Projects."""

from datetime import datetime
from pydantic import BaseModel, Field


# ── Request schemas ──────────────────────────────────────────────────────────

class ProjectCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    product_name: str = Field(..., min_length=1, max_length=255)
    product_category: str = Field(default="", max_length=100)
    company: str = Field(default="", max_length=255)
    description: str | None = None


class ProjectUpdate(BaseModel):
    name: str | None = None
    product_name: str | None = None
    product_category: str | None = None
    company: str | None = None
    status: str | None = None
    description: str | None = None


# ── Response schemas ─────────────────────────────────────────────────────────

class ProjectResponse(BaseModel):
    id: str
    name: str
    audit_id: str
    product_name: str
    product_category: str
    company: str
    status: str
    description: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ProjectStatsResponse(BaseModel):
    """Aggregated statistics for a project."""
    requirements: int = 0
    coverage: int = 0
    supported: int = 0
    partial: int = 0
    missing: int = 0
    conflict: int = 0
    documents: int = 0
    evidence_segments: int = 0
    findings: int = 0
