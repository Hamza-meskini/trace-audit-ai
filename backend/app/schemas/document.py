"""Pydantic schemas for Documents."""

from datetime import datetime
from pydantic import BaseModel, Field


class DocumentResponse(BaseModel):
    id: str
    project_id: str
    filename: str
    original_filename: str
    doc_type: str
    version: str
    page_count: int | None
    file_size: int | None
    processing_status: str
    requirements_linked: int
    uploaded_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class DocumentUploadResponse(BaseModel):
    id: str
    filename: str
    original_filename: str
    doc_type: str
    processing_status: str
    message: str = "Document uploaded successfully"
