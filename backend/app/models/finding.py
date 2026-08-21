"""Finding model — audit findings that need human review."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import String, Text, Integer, DateTime, ForeignKey, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> str:
    return str(uuid.uuid4())


class Finding(Base):
    __tablename__ = "findings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(String(36), ForeignKey("projects.id"), nullable=False)
    requirement_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("requirements.id"), nullable=True)
    finding_code: Mapped[str] = mapped_column(String(50), nullable=False)
    finding_type: Mapped[str] = mapped_column(String(100), nullable=False)
    severity: Mapped[str] = mapped_column(String(50), nullable=False, default="Medium")
    review_state: Mapped[str] = mapped_column(String(50), nullable=False, default="Open")
    assigned_to: Mapped[str | None] = mapped_column(String(255), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    sources_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    category: Mapped[str] = mapped_column(String(100), nullable=False, default="Uncategorized")
    evidence_refs: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)

    # Relationships
    project = relationship("Project", back_populates="findings")
    requirement = relationship("Requirement", back_populates="findings")
