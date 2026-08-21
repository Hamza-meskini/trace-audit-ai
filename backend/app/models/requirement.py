"""Requirement model — extracted or imported requirements with coverage status."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import String, Text, Integer, Float, DateTime, ForeignKey, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> str:
    return str(uuid.uuid4())


class Requirement(Base):
    __tablename__ = "requirements"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(String(36), ForeignKey("projects.id"), nullable=False)
    req_code: Mapped[str] = mapped_column(String(50), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    category: Mapped[str] = mapped_column(String(100), nullable=False, default="Uncategorized")
    source_document: Mapped[str | None] = mapped_column(String(500), nullable=True)
    sources_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    coverage_status: Mapped[str] = mapped_column(String(50), nullable=False, default="Missing")
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    review_state: Mapped[str] = mapped_column(String(50), nullable=False, default="Open")
    severity: Mapped[str] = mapped_column(String(50), nullable=False, default="Medium")
    ai_analysis: Mapped[str | None] = mapped_column(Text, nullable=True)
    ai_recommendation: Mapped[str | None] = mapped_column(Text, nullable=True)
    extracted_parameters: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)

    # Relationships
    project = relationship("Project", back_populates="requirements")
    findings = relationship("Finding", back_populates="requirement", cascade="all, delete-orphan")
    evidence_links = relationship("RequirementEvidence", back_populates="requirement", cascade="all, delete-orphan")


class RequirementEvidence(Base):
    """Link table between requirements and evidence chunks with AI assessment."""
    __tablename__ = "requirement_evidence"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    requirement_id: Mapped[str] = mapped_column(String(36), ForeignKey("requirements.id"), nullable=False)
    evidence_chunk_id: Mapped[str] = mapped_column(String(36), ForeignKey("evidence_chunks.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(100), nullable=False, default="Supporting evidence")
    label: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    highlight: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    # Relationships
    requirement = relationship("Requirement", back_populates="evidence_links")
    evidence_chunk = relationship("EvidenceChunk")
