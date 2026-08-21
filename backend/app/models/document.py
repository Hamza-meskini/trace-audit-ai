"""Document and EvidenceChunk models — uploaded files and their parsed segments."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import String, Text, Integer, DateTime, ForeignKey, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> str:
    return str(uuid.uuid4())


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(String(36), ForeignKey("projects.id"), nullable=False)
    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(500), nullable=False)
    doc_type: Mapped[str] = mapped_column(String(100), nullable=False, default="Other")
    version: Mapped[str] = mapped_column(String(50), nullable=False, default="v1.0")
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    file_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    storage_path: Mapped[str] = mapped_column(String(1000), nullable=False)
    processing_status: Mapped[str] = mapped_column(String(50), nullable=False, default="Queued")
    requirements_linked: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)

    # Relationships
    project = relationship("Project", back_populates="documents")
    evidence_chunks = relationship("EvidenceChunk", back_populates="document", cascade="all, delete-orphan")


class EvidenceChunk(Base):
    __tablename__ = "evidence_chunks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    document_id: Mapped[str] = mapped_column(String(36), ForeignKey("documents.id"), nullable=False)
    page_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    # Relationships
    document = relationship("Document", back_populates="evidence_chunks")
