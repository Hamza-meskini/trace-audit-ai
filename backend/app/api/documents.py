"""Documents API — upload, list, get."""

import os
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.project import Project
from app.models.document import Document
from app.schemas.document import DocumentResponse, DocumentUploadResponse

router = APIRouter(prefix="/projects/{project_id}/documents", tags=["Documents"])

# Maximum upload size: 50 MB
MAX_UPLOAD_SIZE_BYTES = 50 * 1024 * 1024

# Map file extensions and filename keywords to realistic engineering document types
DOC_TYPE_MAP = {
    ".pdf": "Technical specification",
    ".docx": "Technical documentation",
    ".xlsx": "Compliance matrix",
    ".csv": "Compliance matrix",
}


def infer_doc_type(filename: str, ext: str) -> str:
    """Infer realistic engineering document type based on filename keywords."""
    fn = filename.lower()
    if any(k in fn for k in ("srs", "requirement", "prd", "prs", "constraint", "technicalspec", "technical_spec", "system_def", "product_spec")):
        return "Technical specification"
    elif any(k in fn for k in ("test", "report", "lab", "validation", "verification_report", "tr-", "test_log", "measurement")):
        return "Test report"
    elif any(k in fn for k in ("datasheet", "ds-", "supplier", "oem", "component", "part_spec")):
        return "Supplier documentation"
    elif any(k in fn for k in ("matrix", "compliance", "verification_matrix", "rvtm", "traceability_matrix")):
        return "Compliance matrix"
    elif any(k in fn for k in ("risk", "hazard", "fmea", "safety_case", "iso_14971", "iso14971")):
        return "Risk assessment"
    elif any(k in fn for k in ("manual", "guide", "user_manual", "operating_instructions")):
        return "User manual"
    elif any(k in fn for k in ("architecture", "system_architecture", "arch_spec", "interface_spec")):
        return "Architecture specification"
    return DOC_TYPE_MAP.get(ext, "Technical documentation")


@router.get("", response_model=list[DocumentResponse])
async def list_documents(project_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Document)
        .where(Document.project_id == project_id)
        .order_by(Document.uploaded_at.desc())
    )
    return result.scalars().all()


@router.post("", response_model=DocumentUploadResponse, status_code=201)
async def upload_document(
    project_id: str,
    file: UploadFile = File(...),
    doc_type: str = Form(default=""),
    version: str = Form(default="v1.0"),
    db: AsyncSession = Depends(get_db),
):
    # Verify project exists
    result = await db.execute(select(Project).where(Project.id == project_id))
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Project not found")

    # Validate file type
    ext = Path(file.filename or "").suffix.lower()
    allowed = {".pdf", ".docx", ".xlsx", ".csv"}
    if ext not in allowed:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '{ext}'. Allowed: {', '.join(allowed)}",
        )

    # Save file to local storage
    file_id = str(uuid.uuid4())
    safe_filename = f"{file_id}{ext}"
    project_dir = Path(settings.UPLOAD_DIR) / project_id
    project_dir.mkdir(parents=True, exist_ok=True)
    file_path = project_dir / safe_filename

    content = await file.read()
    if len(content) > MAX_UPLOAD_SIZE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File too large ({len(content) // (1024 * 1024)} MB). Maximum upload size is {MAX_UPLOAD_SIZE_BYTES // (1024 * 1024)} MB.",
        )
    file_path.write_bytes(content)

    # Determine doc_type from filename and extension if not explicitly provided
    resolved_type = doc_type if doc_type else infer_doc_type(file.filename or "", ext)

    doc = Document(
        id=file_id,
        project_id=project_id,
        filename=safe_filename,
        original_filename=file.filename or "unknown",
        doc_type=resolved_type,
        version=version,
        file_size=len(content),
        storage_path=str(file_path),
        processing_status="Queued",
    )
    db.add(doc)
    await db.flush()
    await db.refresh(doc)

    return DocumentUploadResponse(
        id=doc.id,
        filename=doc.filename,
        original_filename=doc.original_filename,
        doc_type=doc.doc_type,
        processing_status=doc.processing_status,
    )


@router.get("/{document_id}", response_model=DocumentResponse)
async def get_document(project_id: str, document_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Document).where(Document.id == document_id, Document.project_id == project_id)
    )
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    return doc


@router.delete("/{document_id}", status_code=204)
async def delete_document(project_id: str, document_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Document).where(Document.id == document_id, Document.project_id == project_id)
    )
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    # Remove file from disk
    try:
        os.remove(doc.storage_path)
    except OSError:
        pass

    await db.delete(doc)
