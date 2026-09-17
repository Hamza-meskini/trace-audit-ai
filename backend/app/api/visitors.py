"""API endpoints for visitor lead registration, management, and CSV export."""

import csv
import io
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.visitor import Visitor
from app.schemas.visitor import (
    VisitorCreate,
    VisitorListResponse,
    VisitorOut,
    VisitorRegisterResponse,
)

router = APIRouter(prefix="/visitors", tags=["Visitors"])


def _extract_client_ip(request: Request) -> str | None:
    """Extract real client IP respecting Databricks and reverse proxy headers."""
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    if request.client:
        return request.client.host
    return None


@router.post(
    "",
    response_model=VisitorRegisterResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a website visitor email / lead request",
)
async def register_visitor(
    payload: VisitorCreate,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Store or update a visitor email from the website."""
    normalized_email = payload.email.strip().lower()
    client_ip = _extract_client_ip(request)
    user_agent = request.headers.get("user-agent")

    stmt = select(Visitor).where(Visitor.email == normalized_email)
    result = await db.execute(stmt)
    existing_visitor = result.scalar_one_or_none()

    if existing_visitor:
        if payload.full_name:
            existing_visitor.full_name = payload.full_name
        if payload.company:
            existing_visitor.company = payload.company
        if payload.role:
            existing_visitor.role = payload.role
        if payload.notes:
            existing_visitor.notes = payload.notes
        if payload.source:
            existing_visitor.source = payload.source
        if client_ip:
            existing_visitor.ip_address = client_ip
        if user_agent:
            existing_visitor.user_agent = user_agent
        existing_visitor.updated_at = datetime.now(timezone.utc)

        await db.flush()
        await db.refresh(existing_visitor)
        return VisitorRegisterResponse(
            status="success",
            message="Your email is already registered. Information updated successfully!",
            lead=VisitorOut.model_validate(existing_visitor),
        )

    visitor = Visitor(
        email=normalized_email,
        full_name=payload.full_name,
        company=payload.company,
        role=payload.role,
        notes=payload.notes,
        source=payload.source,
        ip_address=client_ip,
        user_agent=user_agent,
    )
    db.add(visitor)
    await db.flush()
    await db.refresh(visitor)

    return VisitorRegisterResponse(
        status="success",
        message="Thank you! Your email has been registered successfully.",
        lead=VisitorOut.model_validate(visitor),
    )


@router.get(
    "",
    response_model=VisitorListResponse,
    summary="List captured website visitors",
)
async def list_visitors(
    db: Annotated[AsyncSession, Depends(get_db)],
    search: str | None = Query(default=None, description="Search by email, name, or company"),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    """List captured visitor leads with optional search filtering."""
    base_query = select(Visitor)

    if search:
        pattern = f"%{search.strip().lower()}%"
        base_query = base_query.where(
            func.lower(Visitor.email).like(pattern)
            | func.lower(Visitor.full_name).like(pattern)
            | func.lower(Visitor.company).like(pattern)
        )

    count_stmt = select(func.count()).select_from(base_query.subquery())
    total_result = await db.execute(count_stmt)
    total = total_result.scalar_one()

    query = base_query.order_by(Visitor.created_at.desc()).offset(offset).limit(limit)
    result = await db.execute(query)
    visitors = result.scalars().all()

    return VisitorListResponse(
        total=total,
        items=[VisitorOut.model_validate(v) for v in visitors],
    )


@router.get(
    "/export",
    summary="Export captured visitors as CSV",
)
async def export_visitors_csv(
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Generate and download a CSV file of all captured visitor leads."""
    stmt = select(Visitor).order_by(Visitor.created_at.desc())
    result = await db.execute(stmt)
    visitors = result.scalars().all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "ID",
        "Email",
        "Full Name",
        "Company",
        "Role",
        "Source",
        "Registered At (UTC)",
        "Last Updated At (UTC)",
        "Notes",
    ])

    for v in visitors:
        writer.writerow([
            v.id,
            v.email,
            v.full_name or "",
            v.company or "",
            v.role or "",
            v.source or "",
            v.created_at.strftime("%Y-%m-%d %H:%M:%S") if v.created_at else "",
            v.updated_at.strftime("%Y-%m-%d %H:%M:%S") if v.updated_at else "",
            v.notes or "",
        ])

    csv_data = output.getvalue()
    return Response(
        content=csv_data,
        media_type="text/csv",
        headers={
            "Content-Disposition": 'attachment; filename="traceaudit_visitors.csv"',
            "Cache-Control": "no-cache, no-store, must-revalidate",
        },
    )


@router.delete(
    "/{visitor_id}",
    summary="Delete a captured visitor",
)
async def delete_visitor(
    visitor_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Delete a visitor lead record."""
    stmt = select(Visitor).where(Visitor.id == visitor_id)
    result = await db.execute(stmt)
    visitor = result.scalar_one_or_none()

    if not visitor:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Visitor '{visitor_id}' not found",
        )

    await db.delete(visitor)
    return {"status": "success", "message": f"Visitor '{visitor_id}' deleted"}
