"""Project model — top-level audit workspace."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import String, Text, DateTime
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> str:
    return str(uuid.uuid4())


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    audit_id: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    product_name: Mapped[str] = mapped_column(String(255), nullable=False)
    product_category: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    company: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="Draft")
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)

    # Relationships
    documents = relationship("Document", back_populates="project", cascade="all, delete-orphan")
    requirements = relationship("Requirement", back_populates="project", cascade="all, delete-orphan")
    findings = relationship("Finding", back_populates="project", cascade="all, delete-orphan")
