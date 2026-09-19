"""TraceAudit AI — Database Migration and Automatic Deployment Provisioning.

This script runs during deployment (e.g. in app.yaml on Databricks Apps) or via CLI
to ensure the database, catalog, schema, and all Delta Lake / SQLite tables are
created automatically before the web application starts accepting traffic.

Usage:
  python backend/scripts/migrate_database.py [--force-seed] [--check-only]
"""

import argparse
import asyncio
import logging
import os
import sys
import time
from pathlib import Path

# Ensure backend directory is in python path
backend_dir = Path(__file__).resolve().parents[1]
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from app.config import settings
from app.database import Base, get_db_context
from app.models import (
    Project,
    Document,
    EvidenceChunk,
    Requirement,
    RequirementEvidence,
    Finding,
    AppSetting,
    Visitor,
)
from app.seed import seed_database
from sqlalchemy import create_engine, text

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("migration")

EXPECTED_TABLES = [
    "projects",
    "documents",
    "evidence_chunks",
    "requirements",
    "requirement_evidence",
    "findings",
    "app_settings",
    "visitors",
]


def run_databricks_migration(check_only: bool = False, force_seed: bool = False) -> bool:
    """Provision Databricks SQL Warehouse schema and Delta Lake tables."""
    host = settings.effective_databricks_host
    token = settings.effective_databricks_token
    warehouse_id = settings.effective_databricks_warehouse_id
    catalog = settings.effective_databricks_catalog
    schema = settings.effective_databricks_schema

    logger.info("=" * 65)
    logger.info("🚀 Starting Databricks SQL Warehouse Auto-Migration")
    logger.info("=" * 65)
    logger.info("Workspace Host: %s", host or "(not set)")
    logger.info("Warehouse ID:   %s", warehouse_id or "(not set)")
    logger.info("Target Catalog: %s", catalog)
    logger.info("Target Schema:  %s", schema)
    logger.info("Token Status:   %s", "Configured" if token else "MISSING")

    if not host or not token or not warehouse_id:
        logger.error(
            "❌ Missing required Databricks credentials. Ensure DATABRICKS_HOST, "
            "DATABRICKS_TOKEN, and DATABRICKS_SQL_WAREHOUSE_ID are provided."
        )
        return False

    url = settings.databricks_sqlalchemy_url

    # Retry connection in case warehouse is cold-starting
    max_attempts = 4
    engine = None
    for attempt in range(1, max_attempts + 1):
        try:
            logger.info("Connecting to SQL Warehouse (attempt %d/%d)...", attempt, max_attempts)
            engine = create_engine(url, echo=False)
            with engine.connect() as conn:
                res = conn.execute(text("SELECT 1 as alive"))
                row = res.fetchone()
                if row and row[0] == 1:
                    logger.info("✅ Connected to Databricks SQL Warehouse successfully.")
                    break
        except Exception as e:
            logger.warning("Connection attempt %d failed: %s", attempt, e)
            if attempt < max_attempts:
                wait_sec = attempt * 5
                logger.info("Waiting %d seconds for warehouse to become ready...", wait_sec)
                time.sleep(wait_sec)
            else:
                logger.error("❌ Could not connect to Databricks SQL Warehouse after %d attempts.", max_attempts)
                return False

    if check_only:
        with engine.connect() as conn:
            res = conn.execute(text(f"SHOW TABLES IN {catalog}.{schema}"))
            existing = [r[1] for r in res.fetchall()]
            logger.info("Existing tables in %s.%s: %s", catalog, schema, existing)
            return True

    # 1. Ensure Catalog and Schema exist
    with engine.begin() as conn:
        logger.info("Ensuring schema `%s`.`%s` exists in Unity Catalog...", catalog, schema)
        try:
            conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {catalog}.{schema}"))
            logger.info("✅ Schema verified / created.")
        except Exception as e:
            logger.warning("CREATE SCHEMA notice: %s", e)

    # 2. Create all Delta Lake tables via SQLAlchemy DDL
    logger.info("Creating Delta Lake tables via SQLAlchemy metadata...")
    Base.metadata.create_all(bind=engine)
    logger.info("✅ Base.metadata.create_all executed successfully.")

    # 3. Verify all expected tables exist
    with engine.connect() as conn:
        res = conn.execute(text(f"SHOW TABLES IN {catalog}.{schema}"))
        found_tables = {r[1].lower() for r in res.fetchall()}
        logger.info("Found tables in %s.%s: %s", catalog, schema, ", ".join(sorted(found_tables)))

        missing = [t for t in EXPECTED_TABLES if t not in found_tables]
        if missing:
            logger.warning("⚠️ Some expected tables were not reported by SHOW TABLES: %s", missing)
        else:
            logger.info("✅ All %d required Delta Lake tables verified!", len(EXPECTED_TABLES))

        # Check if empty to auto-seed initial data
        res = conn.execute(text(f"SELECT COUNT(*) FROM {catalog}.{schema}.projects"))
        count = res.scalar() or 0
        logger.info("Current project count in `%s`.`%s`.projects: %d", catalog, schema, count)

    if count == 0 or force_seed:
        logger.info("Seeding initial benchmark audit data into Databricks Delta Lake...")
        try:
            async def _seed():
                async with get_db_context() as db:
                    await seed_database(db)
            asyncio.run(_seed())
            logger.info("✅ Database seeded with initial benchmark projects and findings.")
        except Exception as se:
            logger.warning("Could not auto-seed data (non-fatal): %s", se)

    logger.info("=" * 65)
    logger.info("🎉 Databricks SQL Warehouse Migration Complete!")
    logger.info("=" * 65)
    return True


def run_sqlite_migration(check_only: bool = False, force_seed: bool = False) -> bool:
    """Provision local SQLite database tables."""
    logger.info("=" * 65)
    logger.info("🚀 Starting Local SQLite Auto-Migration")
    logger.info("=" * 65)
    logger.info("Database URL: %s", settings.DATABASE_URL)

    raw_path = settings.DATABASE_URL.replace("sqlite+aiosqlite:///", "").replace("sqlite:///", "")
    db_file = Path(raw_path).resolve()
    db_file.parent.mkdir(parents=True, exist_ok=True)
    logger.info("Target file:  %s", db_file)

    async def _init_sqlite():
        from app.database import init_db
        await init_db()
        logger.info("✅ SQLite tables verified / created.")

        async with get_db_context() as db:
            from sqlalchemy import select, func
            count_res = await db.execute(select(func.count()).select_from(Project))
            count = count_res.scalar() or 0
            logger.info("Current project count in SQLite: %d", count)
            if count == 0 or force_seed:
                logger.info("Seeding initial benchmark audit data into SQLite...")
                await seed_database(db)
                logger.info("✅ SQLite seeded with initial benchmark projects.")

    asyncio.run(_init_sqlite())
    logger.info("=" * 65)
    logger.info("🎉 SQLite Migration Complete!")
    logger.info("=" * 65)
    return True


def main():
    parser = argparse.ArgumentParser(description="TraceAudit AI Auto-Migration Script")
    parser.add_argument("--force-seed", action="store_true", help="Force re-seeding even if projects exist")
    parser.add_argument("--check-only", action="store_true", help="Check database readiness without altering tables")
    args = parser.parse_args()

    backend = settings.effective_database_backend
    logger.info("Active Database Backend: %s", backend)

    if backend == "databricks":
        success = run_databricks_migration(check_only=args.check_only, force_seed=args.force_seed)
    else:
        success = run_sqlite_migration(check_only=args.check_only, force_seed=args.force_seed)

    if not success:
        sys.exit(1)


if __name__ == "__main__":
    main()
