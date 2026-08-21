"""TraceAudit AI — FastAPI application entry point."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.database import init_db, async_session
from app.seed import seed_database

from app.api.projects import router as projects_router
from app.api.documents import router as documents_router
from app.api.requirements import router as requirements_router
from app.api.findings import router as findings_router
from app.api.audit import router as audit_router
from app.api.settings import router as settings_router

logger = logging.getLogger("traceaudit")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown lifecycle."""
    # Startup
    logger.info("Initializing database...")
    await init_db()

    # Seed mock data for development
    async with async_session() as db:
        seeded = await seed_database(db)
        if seeded:
            logger.info("Database seeded with mock data.")
        else:
            logger.info("Database already contains data, skipping seed.")

    yield
    # Shutdown (nothing to clean up for now)


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="AI-assisted technical requirements and evidence auditing platform.",
    lifespan=lifespan,
)

# CORS — allow the Vite frontend dev server
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register API routers
app.include_router(projects_router, prefix="/api")
app.include_router(documents_router, prefix="/api")
app.include_router(requirements_router, prefix="/api")
app.include_router(findings_router, prefix="/api")
app.include_router(audit_router, prefix="/api")
app.include_router(settings_router, prefix="/api")


@app.get("/api/health")
async def health_check():
    return {"status": "ok", "version": settings.APP_VERSION}
