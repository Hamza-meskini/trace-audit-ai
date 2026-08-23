"""End-to-End Pipeline Integration Test with Gemini Embeddings."""

import os
import sys
import uuid
import asyncio
from pathlib import Path
import shutil

# Setup path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.database import init_db, async_session
from app.models.project import Project
from app.models.document import Document
from app.models.requirement import Requirement, RequirementEvidence
from app.models.finding import Finding
from app.services.pipeline import run_audit_pipeline
from app.services.embedding import _has_embedding_key


async def run_pipeline_e2e_test():
    print("=" * 70)
    print("     TRACEAUDIT AI - END-TO-END PIPELINE INTEGRATION TEST")
    print("=" * 70)

    print(f"Gemini API Key Active: {_has_embedding_key()}")

    # 1. Initialize DB
    await init_db()

    async with async_session() as db:
        # Create a test project
        project_id = str(uuid.uuid4())
        test_project = Project(
            id=project_id,
            name="E2E Pipeline Integration Test",
            audit_id=f"AUD-{uuid.uuid4().hex[:6].upper()}",
            product_name="Battery Control Unit (BCU-800)",
            product_category="Automotive / Energy",
            company="TraceAudit Test Lab",
            status="Draft",
        )
        db.add(test_project)
        await db.flush()

        # Upload sample benchmark documents to this project
        sample_dir = Path(__file__).resolve().parent.parent / "sample_documents"
        upload_dir = Path(__file__).resolve().parent / "uploads" / project_id
        upload_dir.mkdir(parents=True, exist_ok=True)

        sample_files = list(sample_dir.glob("*.*"))
        print(f"\n[1/4] Ingesting {len(sample_files)} sample documents into test project...")

        for sf in sorted(sample_files):
            dest = upload_dir / sf.name
            shutil.copy(sf, dest)

            doc = Document(
                id=str(uuid.uuid4()),
                project_id=project_id,
                filename=sf.name,
                original_filename=sf.name,
                doc_type="Technical specification" if "01_" in sf.name else "Test report",
                version="v1.0",
                file_size=dest.stat().st_size,
                storage_path=str(dest),
                processing_status="Queued",
            )
            db.add(doc)

        await db.commit()
        print(f"  [OK] Stored {len(sample_files)} documents in database.")

        # 2. Run the Full Audit Pipeline
        print("\n[2/4] Executing run_audit_pipeline() with Hybrid Semantic Embeddings...")
        pipeline_result = await run_audit_pipeline(
            project_id=project_id,
            db=db,
            model="gemini-3.7-flash",
            thinking_level="HIGH",
        )
        print(f"  [OK] Pipeline completed with result: {pipeline_result}")

        # 3. Verify Database Records
        print("\n[3/4] Verifying generated database entities...")
        from sqlalchemy import select

        reqs_res = await db.execute(
            select(Requirement).where(Requirement.project_id == project_id)
        )
        reqs = reqs_res.scalars().all()
        print(f"  -> Requirements extracted: {len(reqs)}")

        evidence_res = await db.execute(
            select(RequirementEvidence)
            .join(Requirement, RequirementEvidence.requirement_id == Requirement.id)
            .where(Requirement.project_id == project_id)
        )
        evidence_links = evidence_res.scalars().all()
        print(f"  -> Evidence links generated: {len(evidence_links)}")

        findings_res = await db.execute(
            select(Finding).where(Finding.project_id == project_id)
        )
        findings = findings_res.scalars().all()
        print(f"  -> Findings generated: {len(findings)}")

        # Status distribution
        status_counts = {}
        for r in reqs:
            status_counts[r.coverage_status] = status_counts.get(r.coverage_status, 0) + 1
        print(f"  -> Coverage Breakdown: {status_counts}")

        # 4. Clean up test project
        print("\n[4/4] Cleaning up test project records...")
        await db.delete(test_project)
        await db.commit()
        if upload_dir.exists():
            shutil.rmtree(upload_dir, ignore_errors=True)

        # Assertions
        assert len(reqs) == 30, f"Expected 30 requirements, got {len(reqs)}"
        assert len(evidence_links) > 15, f"Expected >15 evidence links, got {len(evidence_links)}"
        assert len(findings) > 0, "Expected findings to be generated"
        print("\n" + "=" * 70)
        print(" [PASSED] Pipeline end-to-end integration verified successfully!")
        print("=" * 70)


if __name__ == "__main__":
    asyncio.run(run_pipeline_e2e_test())
