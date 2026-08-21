"""Seed the database with mock data matching the frontend's mock-data.ts.

This ensures the database works correctly and the frontend can display
real data from the API immediately.
"""

from datetime import datetime, timezone, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.project import Project
from app.models.document import Document, EvidenceChunk
from app.models.requirement import Requirement, RequirementEvidence
from app.models.finding import Finding


def _dt(days_ago: int = 0) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days_ago)


async def seed_database(db: AsyncSession) -> bool:
    """Insert mock data if the database is empty. Returns True if seeded."""
    from sqlalchemy import select, func

    count = await db.execute(select(func.count()).select_from(Project))
    if (count.scalar() or 0) > 0:
        return False

    # ── Project ──────────────────────────────────────────────────────────
    project = Project(
        id="proj-001",
        name="Industrial Controller X200",
        audit_id="TA-2026-0042",
        product_name="Industrial Controller X200",
        product_category="Industrial electronic controller",
        company="Atlas Motion Systems",
        status="Analysis complete",
        description="EU technical documentation audit for the X200 industrial electronic controller.",
    )
    project.created_at = _dt(10)
    project.updated_at = _dt(3)
    db.add(project)

    project2 = Project(
        id="proj-002",
        name="Servo Drive S80",
        audit_id="TA-2026-0039",
        product_name="Servo Drive S80",
        product_category="Motion drive electronics",
        company="Atlas Motion Systems",
        status="Analysis complete",
    )
    project2.created_at = _dt(29)
    project2.updated_at = _dt(8)
    db.add(project2)

    project3 = Project(
        id="proj-003",
        name="Sensor Hub H12",
        audit_id="TA-2026-0051",
        product_name="Sensor Hub H12",
        product_category="Industrial sensor gateway",
        company="Atlas Motion Systems",
        status="Analyzing evidence",
    )
    project3.created_at = _dt(5)
    project3.updated_at = _dt(0)
    db.add(project3)

    # ── Documents ────────────────────────────────────────────────────────
    docs_data = [
        ("doc-01", "Product_Specification_X200.pdf", "Technical specification", "v2.4", 48, 126, "Indexed", 0),
        ("doc-02", "Safety_Test_Report.pdf", "Test report", "v1.8", 73, 84, "Indexed", 0),
        ("doc-03", "Risk_Assessment_X200.xlsx", "Risk assessment", "v3.1", None, 57, "Indexed", 1),
        ("doc-04", "Supplier_Datasheet_MainController.pdf", "Supplier documentation", "v4.0", 18, 43, "Indexed", 1),
        ("doc-05", "Environmental_Test_Report.pdf", "Test report", "v2.2", 41, 72, "Indexed", 2),
        ("doc-06", "User_Manual_X200.docx", "Technical documentation", "v5.0", 62, 39, "Processing", 3),
    ]

    docs = {}
    for doc_id, name, dtype, version, pages, linked, status, days in docs_data:
        doc = Document(
            id=doc_id,
            project_id="proj-001",
            filename=name,
            original_filename=name,
            doc_type=dtype,
            version=version,
            page_count=pages,
            file_size=1024 * 50,
            storage_path=f"./uploads/proj-001/{name}",
            processing_status=status,
            requirements_linked=linked,
        )
        doc.uploaded_at = _dt(days)
        doc.updated_at = _dt(days)
        db.add(doc)
        docs[doc_id] = doc

    # ── Evidence Chunks ──────────────────────────────────────────────────
    evidence_data = [
        ("ev-01", "doc-01", 12, "Operating input voltage: 18–32 V DC."),
        ("ev-02", "doc-05", 19, "The controller successfully operated at 18 V, 24 V and 32 V."),
        ("ev-03", "doc-02", 34, "Over-voltage clamping verified at 36 V with no functional degradation."),
        ("ev-04", "doc-05", 31, "Thermal cycling performed between -20°C and +60°C."),
        ("ev-05", "doc-01", 8, "Ambient operating temperature: -20°C to +70°C."),
        ("ev-06", "doc-01", 12, "Operating input voltage: 18–32 V DC."),
        ("ev-07", "doc-04", 4, "Recommended input voltage range: 18–30 V DC."),
        ("ev-08", "doc-05", 19, "The controller successfully operated at 18 V, 24 V and 32 V."),
        ("ev-09", "doc-01", 21, "Enclosure rating: IP54 per housing qualification test plan."),
        ("ev-10", "doc-01", 39, "Firmware images are validated using a vendor signature check at boot."),
        ("ev-11", "doc-06", 6, "Section 2 – Installation safety: disconnect supply before wiring."),
        ("ev-12", "doc-05", 27, "Random vibration test completed per panel mount configuration."),
        ("ev-13", "doc-01", 41, "Diagnostic port requires a service credential."),
        ("ev-14", "doc-06", 44, "Connect to the diagnostic port to read live values; no login required."),
        ("ev-15", "doc-01", 45, "MTBF: 250,000 hours (calculated)."),
    ]

    chunks = {}
    for ev_id, doc_id, page, content in evidence_data:
        chunk = EvidenceChunk(
            id=ev_id,
            document_id=doc_id,
            page_number=page,
            chunk_index=0,
            content=content,
        )
        db.add(chunk)
        chunks[ev_id] = chunk

    # ── Requirements ─────────────────────────────────────────────────────
    reqs_data = [
        ("req-001", "REQ-001", "Operating voltage must remain within 18–32 V DC", "Electrical", 3, "Supported", 98, "Reviewed", "Medium", "Product_Specification_X200.pdf", None, None),
        ("req-002", "REQ-002", "Device shall provide over-voltage protection", "Safety", 2, "Supported", 95, "Reviewed", "High", "Safety_Test_Report.pdf", None, None),
        ("req-003", "REQ-003", "Device shall operate from -20°C to +70°C", "Environmental", 2, "Partial", 87, "Needs review", "Medium", "Environmental_Test_Report.pdf",
         "Test evidence covers only part of the declared temperature range. No test record was identified above +60°C.",
         "Extend environmental testing to +70°C or align the declared operating range with available test evidence."),
        ("req-004", "REQ-004", "Manufacturer shall document identified product risks", "Safety", 0, "Missing", 94, "Open", "Critical", "Risk_Assessment_X200.xlsx",
         "No evidence segment in the indexed document set addresses this requirement.",
         "Upload the signed risk assessment record covering identified product risks."),
        ("req-005", "REQ-005", "Controller input voltage tolerance shall comply with supplier specification", "Electrical", 3, "Conflict", 92, "Needs review", "High", "Supplier_Datasheet_MainController.pdf",
         "The available evidence indicates a potential discrepancy between the product specification and supplier documentation. The product specification allows operation up to 32 V, while the supplier datasheet specifies a maximum recommended input voltage of 30 V.",
         "Review the supplier specification and confirm the permitted operating range before final approval."),
        ("req-006", "REQ-006", "Enclosure shall provide IP54 ingress protection", "Mechanical", 2, "Supported", 96, "Reviewed", "Low", "Product_Specification_X200.pdf", None, None),
        ("req-007", "REQ-007", "Firmware update packages shall be cryptographically signed", "Cybersecurity", 1, "Partial", 81, "Needs review", "High", "Product_Specification_X200.pdf",
         "Signature verification is described, but no key management evidence was identified.",
         "Provide key management and signing process documentation."),
        ("req-008", "REQ-008", "User manual shall include installation safety instructions", "Documentation", 1, "Supported", 93, "Reviewed", "Low", "User_Manual_X200.docx", None, None),
        ("req-009", "REQ-009", "Product shall withstand 4 kV surge on power inputs", "Electrical", 0, "Missing", 90, "Open", "High", "Safety_Test_Report.pdf",
         "No surge immunity test record was identified in the indexed document set.",
         "Upload the surge immunity test report for the power input circuit."),
        ("req-010", "REQ-010", "Vibration resistance shall be documented for panel mounting", "Mechanical", 2, "Supported", 91, "Reviewed", "Low", "Environmental_Test_Report.pdf", None, None),
        ("req-011", "REQ-011", "Access to diagnostic port shall require authentication", "Cybersecurity", 3, "Conflict", 88, "Needs review", "Medium", "Product_Specification_X200.pdf",
         "The specification and user manual describe different access control behaviour for the diagnostic port.",
         "Confirm the shipped behaviour and align the documentation set."),
        ("req-012", "REQ-012", "Declared MTBF shall be supported by reliability data", "Documentation", 1, "Partial", 84, "Needs review", "Medium", "Product_Specification_X200.pdf",
         "The calculation method and input data set were not identified.",
         "Attach the reliability prediction worksheet used for the MTBF figure."),
    ]

    reqs = {}
    for (rid, code, title, cat, src_count, cov, conf, rev, sev, src_doc, analysis, rec) in reqs_data:
        req = Requirement(
            id=rid,
            project_id="proj-001",
            req_code=code,
            title=title,
            category=cat,
            sources_count=src_count,
            coverage_status=cov,
            confidence=conf,
            review_state=rev,
            severity=sev,
            source_document=src_doc,
            ai_analysis=analysis,
            ai_recommendation=rec,
        )
        db.add(req)
        reqs[rid] = req

    # ── Requirement ↔ Evidence Links ─────────────────────────────────────
    evidence_links = [
        ("req-001", "ev-01", "Supports requirement", "Product Specification", None),
        ("req-001", "ev-02", "Supporting evidence", "Test Report", None),
        ("req-002", "ev-03", "Supports requirement", "Safety Test Report", None),
        ("req-003", "ev-04", "Potential conflict", "Environmental Test Report", "+60°C"),
        ("req-003", "ev-05", "Supports requirement", "Product Specification", None),
        ("req-005", "ev-06", "Supports requirement", "Product Specification", "32 V"),
        ("req-005", "ev-07", "Potential conflict", "Supplier Datasheet", "30 V"),
        ("req-005", "ev-08", "Supporting evidence", "Test Report", None),
        ("req-006", "ev-09", "Supports requirement", "Product Specification", None),
        ("req-007", "ev-10", "Supporting evidence", "Product Specification", None),
        ("req-008", "ev-11", "Supports requirement", "User Manual", None),
        ("req-010", "ev-12", "Supports requirement", "Environmental Test Report", None),
        ("req-011", "ev-13", "Supports requirement", "Product Specification", None),
        ("req-011", "ev-14", "Potential conflict", "User Manual", "no login required"),
        ("req-012", "ev-15", "Supporting evidence", "Product Specification", None),
    ]

    for req_id, ev_id, status, label, highlight in evidence_links:
        link = RequirementEvidence(
            requirement_id=req_id,
            evidence_chunk_id=ev_id,
            status=status,
            label=label,
            highlight=highlight,
        )
        db.add(link)

    # ── Findings ─────────────────────────────────────────────────────────
    findings_data = [
        ("find-01", "F-001", "req-005", "Potential conflict", "High", "Needs review", "A. Benali", 3, "Electrical", 0),
        ("find-02", "F-002", "req-004", "Missing evidence", "Critical", "Open", None, 0, "Safety", 0),
        ("find-03", "F-003", "req-003", "Partial evidence", "Medium", "Needs review", "L. Fischer", 2, "Environmental", 0),
        ("find-04", "F-004", "req-009", "Missing evidence", "High", "Open", None, 0, "Electrical", 1),
        ("find-05", "F-005", "req-011", "Potential conflict", "Medium", "Needs review", "S. Novak", 3, "Cybersecurity", 1),
        ("find-06", "F-006", "req-007", "Partial evidence", "High", "Needs review", "S. Novak", 1, "Cybersecurity", 2),
        ("find-07", "F-007", "req-012", "Ambiguous requirement", "Medium", "Open", "L. Fischer", 1, "Documentation", 2),
        ("find-08", "F-008", None, "Duplicate requirement", "Low", "Reviewed", "A. Benali", 2, "Electrical", 3),
        ("find-09", "F-009", None, "Unsupported requirement", "High", "Open", None, 0, "Documentation", 3),
        ("find-10", "F-010", None, "Partial evidence", "High", "Needs review", "A. Benali", 3, "Electrical", 3),
    ]

    for (fid, code, req_id, ftype, sev, state, assigned, sources, cat, days) in findings_data:
        finding = Finding(
            id=fid,
            project_id="proj-001",
            requirement_id=req_id,
            finding_code=code,
            finding_type=ftype,
            severity=sev,
            review_state=state,
            assigned_to=assigned,
            sources_count=sources,
            category=cat,
        )
        finding.created_at = _dt(days)
        finding.updated_at = _dt(days)
        db.add(finding)

    await db.commit()
    return True
