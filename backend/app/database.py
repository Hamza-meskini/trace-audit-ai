"""TraceAudit AI — Database engine and session management.

Supports dual-mode database persistence:
1. Local development: SQLite via aiosqlite (async).
2. Production on Databricks Apps: Databricks SQL Warehouse via DatabricksDialect (Delta Lake).
"""

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator
from sqlalchemy import create_engine
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
        if exc_type is not None:
            await self.rollback()
        else:
            await self.commit()
        await self.close()


# Initialize active engine based on configured database backend
active_backend = settings.effective_database_backend
logger.info("Initializing database subsystem with backend: %s", active_backend)

if active_backend == "databricks":
    url = settings.databricks_sqlalchemy_url
    logger.info("Using Databricks SQL Warehouse engine (%s.%s)", settings.DATABRICKS_CATALOG, settings.DATABRICKS_SCHEMA)
    sync_engine = create_engine(url, echo=settings.DEBUG, pool_pre_ping=True)
    databricks_session_factory = sessionmaker(bind=sync_engine, expire_on_commit=False)
    engine = None
    async_session = None
else:
    logger.info("Using local SQLite engine: %s", settings.DATABASE_URL)
    engine = create_async_engine(settings.DATABASE_URL, echo=settings.DEBUG)
    async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    sync_engine = None
    databricks_session_factory = None


async def get_db() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency that yields an async database session."""
    if settings.effective_database_backend == "databricks":
        sync_sess = databricks_session_factory()
        session = AsyncDatabricksSession(sync_sess)
        try:
            yield session  # type: ignore[misc]
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
    else:
        async with async_session() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise


@asynccontextmanager
async def get_db_context() -> AsyncIterator[AsyncSession]:
    """Async context manager yielding a database session for background or startup lifecycle tasks."""
    async for session in get_db():
        yield session
        break


async def init_db() -> None:
    """Create all tables. Called at application startup."""
    backend = settings.effective_database_backend
    if backend == "databricks":
        logger.info(
            "Creating Delta Lake tables on Databricks SQL Warehouse in catalog '%s', schema '%s'...",
            settings.DATABRICKS_CATALOG,
            settings.DATABRICKS_SCHEMA,
        )
        def _sync_init():
            with sync_engine.connect() as conn:
                # Ensure catalog and schema exist
                try:
                    conn.exec_driver_sql(f"CREATE SCHEMA IF NOT EXISTS {settings.DATABRICKS_CATALOG}.{settings.DATABRICKS_SCHEMA}")
                except Exception as e:
                    logger.warning("Could not auto-create schema: %s", e)
            Base.metadata.create_all(bind=sync_engine)

        await asyncio.to_thread(_sync_init)
        logger.info("Databricks Delta Lake tables initialized successfully.")
    else:
        logger.info("Initializing local SQLite database tables...")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("Local SQLite database tables initialized successfully.")
