"""Utility script to verify Databricks SQL Warehouse connectivity and sync tables.

Usage:
  python backend/scripts/sync_db_to_databricks.py [--seed] [--dry-run]
"""

import argparse
import logging
import os
import sqlite3
import sys
from pathlib import Path

# Add backend directory to sys.path
backend_dir = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(backend_dir))

from app.config import settings
from app.database import Base
from sqlalchemy import create_engine, text

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("sync_databricks")


def parse_args():
    parser = argparse.ArgumentParser(description="Databricks SQL Warehouse table setup and sync")
    parser.add_argument("--seed", action="store_true", help="Sync data from local SQLite database")
    parser.add_argument("--dry-run", action="store_true", help="Verify configuration without modifying tables")
    return parser.parse_args()


def main():
    args = parse_args()

    host = settings.effective_databricks_host
    token = settings.effective_databricks_token
    warehouse_id = settings.effective_databricks_warehouse_id
    catalog = settings.effective_databricks_catalog
    schema = settings.effective_databricks_schema

    logger.info("=== Databricks SQL Warehouse Configuration ===")
    logger.info("Host:         %s", host or "(not set)")
    logger.info("Warehouse ID: %s", warehouse_id or "(not set)")
    logger.info("Catalog:      %s", catalog)
    logger.info("Schema:       %s", schema)
    logger.info("Token:        %s", "***" + token[-4:] if len(token) > 4 else "(not set)")

    if not host or not token or not warehouse_id:
        logger.error(
            "Missing required Databricks credentials. Ensure DATABRICKS_HOST, DATABRICKS_TOKEN, "
            "and DATABRICKS_SQL_WAREHOUSE_ID are configured in .env or app.yaml."
        )
        sys.exit(1)

    url = settings.databricks_sqlalchemy_url
    logger.info("Connecting to Databricks SQL Warehouse...")

    try:
        engine = create_engine(url, echo=False)
        with engine.connect() as conn:
            res = conn.execute(text("SELECT current_version() as dbr_version"))
            row = res.fetchone()
            logger.info("Connected successfully! Databricks Runtime / Engine: %s", row[0] if row else "OK")

            if args.dry_run:
                logger.info("Dry run complete. Connection verified.")
                return

            # Ensure schema exists
            logger.info("Ensuring schema %s.%s exists...", catalog, schema)
            conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {catalog}.{schema}"))

            # Create Delta Lake tables
            logger.info("Creating Delta Lake tables via SQLAlchemy DDL...")
            Base.metadata.create_all(bind=engine)
            logger.info("Tables created / verified in %s.%s successfully!", catalog, schema)

            # Check existing tables
            res = conn.execute(text(f"SHOW TABLES IN {catalog}.{schema}"))
            tables = [r[1] for r in res.fetchall()]
            logger.info("Available Delta tables in %s.%s: %s", catalog, schema, ", ".join(tables))

    except Exception as ex:
        logger.error("Failed to connect or initialize Databricks SQL Warehouse: %s", ex, exc_info=True)
        sys.exit(1)

    # Optional seeding from local SQLite database
    if args.seed:
        sqlite_file = Path("traceaudit.db")
        if not sqlite_file.is_file():
            sqlite_file = backend_dir / "traceaudit.db"
        
        if sqlite_file.is_file():
            logger.info("Reading seed records from local SQLite (%s)...", sqlite_file)
            sq_conn = sqlite3.connect(sqlite_file)
            sq_conn.row_factory = sqlite3.Row
            
            with engine.begin() as d_conn:
                for table_name in ["projects", "requirements", "findings", "app_settings"]:
                    try:
                        cursor = sq_conn.cursor()
                        cursor.execute(f"SELECT * FROM {table_name}")
                        rows = cursor.fetchall()
                        if rows:
                            cols = [c[0] for c in cursor.description]
                            col_list = ", ".join(cols)
                            val_placeholders = ", ".join([f":{c}" for c in cols])
                            logger.info("Syncing %d rows to %s.%s.%s...", len(rows), catalog, schema, table_name)
                            for r in rows:
                                row_dict = dict(r)
                                stmt = text(f"MERGE INTO {catalog}.{schema}.{table_name} AS target USING (SELECT {val_placeholders}) AS source ON target.id = source.id WHEN MATCHED THEN UPDATE SET * WHEN NOT MATCHED THEN INSERT *") if "id" in cols else text(f"INSERT INTO {catalog}.{schema}.{table_name} ({col_list}) VALUES ({val_placeholders})")
                                try:
                                    d_conn.execute(stmt, row_dict)
                                except Exception:
                                    # Fallback simple insert if merge not applicable
                                    insert_stmt = text(f"INSERT INTO {catalog}.{schema}.{table_name} ({col_list}) VALUES ({val_placeholders})")
                                    d_conn.execute(insert_stmt, row_dict)
                    except Exception as e:
                        logger.warning("Could not sync table %s: %s", table_name, e)
            logger.info("Local SQLite data synced to Databricks SQL Warehouse!")


if __name__ == "__main__":
    main()
