"""Documents API — upload, list, get."""

import os
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Query
from fastapi.responses import FileResponse, Response
from starlette.concurrency import run_in_threadpool
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.project import Project
from app.models.document import Document, EvidenceChunk
from app.api.requirements import block_response, public_metadata, chunk_pages
from app.schemas.document import DocumentResponse, DocumentUploadResponse
from app.services import audit_progress

router = APIRouter(prefix="/projects/{project_id}/documents", tags=["Documents"])

# Maximum upload size: 50 MB
MAX_UPLOAD_SIZE_BYTES = 50 * 1024 * 1024


def _require_idle(project_id):
    job = audit_progress.latest(project_id)
    if job and job["status"] in ("queued", "running"):
        raise HTTPException(409, "Documents cannot change while this audit is running")

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

    _require_idle(project_id)

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
    _require_idle(project_id)
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


async def _scoped_document(db, project_id, document_id):
    doc = (await db.execute(select(Document).where(
        Document.id == document_id, Document.project_id == project_id,
    ))).scalar_one_or_none()
    if doc is None:
        raise HTTPException(404, "Document not found")
    return doc


def _source_path(doc):
    path = Path(doc.storage_path).resolve()
    root = Path(settings.UPLOAD_DIR).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise HTTPException(404, "Original file is unavailable in managed document storage")
    return path


@router.get("/{document_id}/file")
async def download_source(project_id: str, document_id: str, db: AsyncSession = Depends(get_db)):
    doc = await _scoped_document(db, project_id, document_id)
    path = _source_path(doc)
    return FileResponse(path, filename=doc.original_filename,
                        content_disposition_type="inline" if path.suffix.lower() == ".pdf" else "attachment")


def _render_original_page(path, page_number):
    import fitz
    with fitz.open(path) as pdf:
        if not 1 <= page_number <= len(pdf):
            raise HTTPException(404, "Page not found")
        page = pdf[page_number - 1]
        scale = min(1.6, 2200 / max(page.rect.width, page.rect.height, 1))
        return page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False).tobytes("png")


def _pdf_page_count(path):
    import fitz
    with fitz.open(path) as pdf:
        return len(pdf)


@router.get("/{document_id}/pages/{page_number}.png")
async def original_page(project_id: str, document_id: str, page_number: int,
                        db: AsyncSession = Depends(get_db)):
    doc = await _scoped_document(db, project_id, document_id)
    path = _source_path(doc)
    if path.suffix.lower() != ".pdf":
        raise HTTPException(415, "Page previews are available for PDF originals; use extracted blocks or download")
    png = await run_in_threadpool(_render_original_page, path, page_number)
    return Response(png, media_type="image/png", headers={"Cache-Control": "private, max-age=300"})


@router.get("/{document_id}/inspection")
async def inspect_document(project_id: str, document_id: str, page: int = Query(1, ge=1),
                           db: AsyncSession = Depends(get_db)):
    doc = await _scoped_document(db, project_id, document_id)
    chunks = (await db.execute(select(EvidenceChunk).where(
        EvidenceChunk.document_id == doc.id,
    ).order_by(EvidenceChunk.chunk_index))).scalars().all()
    metadata = public_metadata(chunks[0].metadata_json) if chunks else {}
    available_pages = sorted({page for chunk in chunks for page in chunk_pages(chunk)})
    document_response = DocumentResponse.model_validate(doc)
    is_pdf = Path(doc.filename).suffix.lower() == ".pdf"
    if is_pdf and not document_response.page_count:
        try:
            document_response.page_count = await run_in_threadpool(_pdf_page_count, _source_path(doc))
        except (HTTPException, RuntimeError, ValueError):
            # Extraction and download remain available even if a preview cannot be rendered.
            pass
    return {
        "document": document_response,
        "is_pdf": is_pdf,
        "available_pages": available_pages,
        "profile": metadata.get("document_profile", {}),
        "diagnostics": metadata.get("document_diagnostics", {}),
        "blocks": [block_response(chunk) for chunk in chunks
                   if page in chunk_pages(chunk) or (not chunk_pages(chunk) and page == 1)],
        "counts": {"blocks": len(chunks),
                   "tables": sum((chunk.metadata_json or {}).get("block_type") == "table" for chunk in chunks),
                   "figures": sum((chunk.metadata_json or {}).get("block_type") == "figure" for chunk in chunks)},
    }
