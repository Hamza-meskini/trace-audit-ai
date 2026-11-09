"""Admin endpoints for previously captured visitor leads (list, export, delete).

The public capture endpoint (POST /api/visitors) was removed: nothing in the
frontend called it, and it collected e-mail addresses, IP addresses and user
agents. These remaining endpoints let the owner export and then erase the
leads already stored; see backend/scripts/purge_visitor_leads.py.
"""

import csv
import io
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.visitor import Visitor
from app.schemas.visitor import VisitorListResponse, VisitorOut

router = APIRouter(prefix="/visitors", tags=["Visitors"])


def _csv_cell(value: str) -> str:
    """Keep untrusted spreadsheet cells from being interpreted as formulas."""
    return "'" + value if value.lstrip().startswith(("=", "+", "-", "@")) or value.startswith(("\t", "\r", "\n")) else value


@router.get(
    "",
    response_model=VisitorListResponse,
    summary="List captured website visitors",
)
async def list_visitors(
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
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
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
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
        writer.writerow([_csv_cell(value) for value in [
            v.id,
            v.email,
            v.full_name or "",
            v.company or "",
            v.role or "",
            v.source or "",
            v.created_at.strftime("%Y-%m-%d %H:%M:%S") if v.created_at else "",
            v.updated_at.strftime("%Y-%m-%d %H:%M:%S") if v.updated_at else "",
            v.notes or "",
        ]])

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
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
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
