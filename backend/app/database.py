"""TraceAudit AI — Database engine and session management.

Supports dual-mode database persistence:
1. Local development: SQLite via aiosqlite (async).
2. Production on Databricks Apps: Databricks SQL Warehouse via DatabricksDialect (Delta Lake).
"""

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator
from sqlalchemy import create_engine, event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings

logger = logging.getLogger("app.database")


class Base(DeclarativeBase):
    """SQLAlchemy declarative base for all models."""
    pass


class AsyncDatabricksSession:
    """Asynchronous wrapper around a synchronous Databricks SQLAlchemy Session.

    Offloads blocking DBAPI calls (over HTTPS / Thrift to Databricks SQL Warehouse)
    to a background worker thread via asyncio.to_thread, keeping FastAPI's event
    loop non-blocking while exposing the exact AsyncSession method signatures.
    """

    def __init__(self, sync_session: Session):
        self._sync_session = sync_session

    async def execute(self, statement: Any, *args: Any, **kwargs: Any) -> Any:
        return await asyncio.to_thread(self._sync_session.execute, statement, *args, **kwargs)

    async def scalars(self, statement: Any, *args: Any, **kwargs: Any) -> Any:
        res = await asyncio.to_thread(self._sync_session.execute, statement, *args, **kwargs)
        return res.scalars()

    def add(self, instance: Any) -> None:
        self._sync_session.add(instance)

    def add_all(self, instances: Any) -> None:
        self._sync_session.add_all(instances)

    async def flush(self) -> None:
        await asyncio.to_thread(self._sync_session.flush)

    async def refresh(self, instance: Any, *args: Any, **kwargs: Any) -> None:
        await asyncio.to_thread(self._sync_session.refresh, instance, *args, **kwargs)

    async def commit(self) -> None:
        await asyncio.to_thread(self._sync_session.commit)

    async def rollback(self) -> None:
        await asyncio.to_thread(self._sync_session.rollback)

    async def delete(self, instance: Any) -> None:
        await asyncio.to_thread(self._sync_session.delete, instance)

    async def get(self, entity: Any, ident: Any, *args: Any, **kwargs: Any) -> Any:
        return await asyncio.to_thread(self._sync_session.get, entity, ident, *args, **kwargs)

    async def close(self) -> None:
        await asyncio.to_thread(self._sync_session.close)

    async def __aenter__(self) -> "AsyncDatabricksSession":
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        try:
            if exc_type is not None:
                await self.rollback()
            else:
                try:
                    await self.commit()
                except BaseException:
                    await self.rollback()
                    raise
        finally:
            await self.close()


# Initialize active engine based on configured database backend
active_backend = settings.effective_database_backend
logger.info("Initializing database subsystem with backend: %s", active_backend)

# Foreign-key lookups every API route and the pipeline filter on. Delta Lake
# has no secondary indexes, so these are created for SQLite only.
SQLITE_INDEXES = (
    ("ix_documents_project_id", "documents", "project_id"),
    ("ix_evidence_chunks_document_id", "evidence_chunks", "document_id"),
    ("ix_requirements_project_id", "requirements", "project_id"),
    ("ix_findings_project_id", "findings", "project_id"),
    ("ix_findings_requirement_id", "findings", "requirement_id"),
    ("ix_requirement_evidence_requirement_id", "requirement_evidence", "requirement_id"),
    ("ix_requirement_evidence_chunk_id", "requirement_evidence", "evidence_chunk_id"),
)


def configure_databricks_batch_inserts(target_engine: Any) -> None:
    """Send ORM inserts as multi-row VALUES statements.

    The connector's ``executemany`` issues one sequential warehouse request
    per row, so flushing N evidence chunks costs N round trips. The dialect
    supports multi-row VALUES; capping parameters per statement keeps each
    request well inside the warehouse's native-parameter limits.
    """
    target_engine.dialect.use_insertmanyvalues = True
    target_engine.dialect.use_insertmanyvalues_wo_returning = True
    target_engine.dialect.insertmanyvalues_max_parameters = max(
        16, int(settings.DATABRICKS_INSERT_MAX_PARAMETERS)
    )


def _configure_sqlite_connection(dbapi_connection: Any, _record: Any) -> None:
    """Let API requests and the audit worker share one SQLite file.

    WAL lets readers proceed while a writer holds the lock, and the busy
    timeout makes a second writer wait instead of failing immediately with
    ``database is locked``.
    """
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute(f"PRAGMA busy_timeout={int(settings.SQLITE_BUSY_TIMEOUT_MS)}")
        cursor.execute("PRAGMA journal_mode=WAL")
    finally:
        cursor.close()


if active_backend == "databricks":
    url = settings.databricks_sqlalchemy_url
    logger.info("Using Databricks SQL Warehouse engine (%s.%s)", settings.effective_databricks_catalog, settings.effective_databricks_schema)
    sync_engine = create_engine(url, echo=settings.DEBUG, pool_pre_ping=True)
    if settings.DATABRICKS_BATCH_INSERTS:
        configure_databricks_batch_inserts(sync_engine)
    databricks_session_factory = sessionmaker(bind=sync_engine, expire_on_commit=False)
    engine = None
    async_session = None
else:
    db_url = settings.effective_database_url
    logger.info("Using local SQLite engine: %s", db_url)
    engine = create_async_engine(db_url, echo=settings.DEBUG)
    event.listen(engine.sync_engine, "connect", _configure_sqlite_connection)
    async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    sync_engine = None
    databricks_session_factory = None


@asynccontextmanager
async def get_db_context() -> AsyncIterator[AsyncSession]:
    """Own a complete transaction, including cancellation and cleanup."""
    if settings.effective_database_backend == "databricks":
        sync_sess = databricks_session_factory()
        session = AsyncDatabricksSession(sync_sess)
        try:
            yield session  # type: ignore[misc]
            await session.commit()
        except BaseException:
            await session.rollback()
            raise
        finally:
            await session.close()
    else:
        async with async_session() as session:
            try:
                yield session
                await session.commit()
            except BaseException:
                await session.rollback()
                raise


async def get_db() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency using the same transaction lifecycle as workers."""
    async with get_db_context() as session:
        yield session


async def init_db() -> None:
    """Create all tables. Called at application startup."""
    backend = settings.effective_database_backend
    if backend == "databricks":
        logger.info(
            "Creating Delta Lake tables on Databricks SQL Warehouse in catalog '%s', schema '%s'...",
            settings.effective_databricks_catalog,
            settings.effective_databricks_schema,
        )
        def _sync_init():
            with sync_engine.connect() as conn:
                # Ensure catalog and schema exist
                try:
                    conn.exec_driver_sql(f"CREATE SCHEMA IF NOT EXISTS {settings.effective_databricks_catalog}.{settings.effective_databricks_schema}")
                except Exception as e:
                    logger.warning("Could not auto-create schema: %s", e)
                try:
                    Base.metadata.create_all(bind=sync_engine)
                except Exception as ddl_err:
                    if any(term in str(ddl_err) for term in ("UNIQUE", "INDEX", "PARSE_SYNTAX_ERROR")):
                        logger.warning("Delta Lake DDL notice: %s", ddl_err)
                    else:
                        raise

                # Reconcile any missing columns on existing Delta tables
                try:
                    from sqlalchemy import Integer, Float, DateTime, Text, String, text
                    from app.models.types import JSONType

                    def _type_str(sa_type) -> str:
                        if isinstance(sa_type, Integer):
                            return "INT"
                        elif isinstance(sa_type, Float):
                            return "DOUBLE"
                        elif isinstance(sa_type, DateTime):
                            return "TIMESTAMP_NTZ"
                        elif isinstance(sa_type, (String, Text, JSONType)):
                            return "STRING"
                        return "STRING"

                    catalog = settings.effective_databricks_catalog
                    schema = settings.effective_databricks_schema
                    for table in Base.metadata.sorted_tables:
                        t_name = table.name
                        try:
                            res = conn.execute(text(f"DESCRIBE TABLE {catalog}.{schema}.{t_name}"))
                            existing_cols = {r[0].lower() for r in res.fetchall()}
                        except Exception:
                            continue
                        for col in table.columns:
                            if col.name.lower() not in existing_cols:
                                col_type = _type_str(col.type)
                                logger.info("⚡ Adding missing column %s.%s (%s)...", t_name, col.name, col_type)
                                try:
                                    conn.execute(text(f"ALTER TABLE {catalog}.{schema}.{t_name} ADD COLUMN ({col.name} {col_type})"))
                                except Exception as alter_err:
                                    logger.warning("Could not add column %s to %s: %s", col.name, t_name, alter_err)
                except Exception as rec_err:
                    logger.warning("Delta Lake schema reconciliation notice: %s", rec_err)

        await asyncio.to_thread(_sync_init)
        logger.info("Databricks Delta Lake tables initialized successfully.")
    else:
        logger.info("Initializing local SQLite database tables...")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            # create_all skips indexes on tables that already exist, so add
            # them explicitly for databases created before they were declared.
            for index_name, table_name, column_name in SQLITE_INDEXES:
                await conn.exec_driver_sql(
                    f"CREATE INDEX IF NOT EXISTS {index_name} ON {table_name} ({column_name})"
                )
        logger.info("Local SQLite database tables initialized successfully.")
