"""TraceAudit AI — FastAPI application entry point."""

import logging
from contextlib import asynccontextmanager

from pathlib import Path
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse

from app.config import settings
from app.database import init_db, async_session
from app.seed import seed_database
from app.api.settings import load_persisted_settings

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
    from app.services.audit_progress import recover_interrupted
    from app.services import audit_worker
    recover_interrupted(requeue=True)

    # Seed mock data for development
    async with async_session() as db:
        seeded = await seed_database(db)
        if seeded:
            logger.info("Database seeded with mock data.")
        else:
            logger.info("Database already contains data, skipping seed.")
        await load_persisted_settings(db)

    audit_worker.start()
    try:
        yield
    finally:
        await audit_worker.stop()


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="AI-assisted technical requirements and evidence auditing platform.",
    lifespan=lifespan,
    root_path=settings.ROOT_PATH,
)

# CORS — allow all local development origins (5173, 8080, 3000, etc.)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS or ["http://localhost:5173", "http://localhost:8080", "http://localhost:3000"],
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
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


@app.get("/api/me")
async def current_user(request: Request):
    """Expose authenticated user identity from Databricks SSO proxy headers."""
    user = (
        request.headers.get("X-Forwarded-User")
        or request.headers.get("X-Databricks-User")
        or request.headers.get("X-Forwarded-Email")
    )
    if user:
        email = request.headers.get("X-Forwarded-Email", user)
        return {"username": user, "email": email, "authenticated": True}
    return {"username": "Local reviewer", "email": None, "authenticated": False}


def _resolve_frontend_dir() -> Path | None:
    """Find the compiled React frontend directory."""
    candidates = [
        Path(settings.FRONTEND_DIR),
        Path(__file__).resolve().parent.parent.parent / "dist" / "client",
        Path(__file__).resolve().parent.parent / "dist" / "client",
        Path("dist/client"),
        Path("static"),
    ]
    for p in candidates:
        if p.is_dir() and (p / "index.html").is_file():
            return p.resolve()
    for p in candidates:
        if p.is_dir():
            return p.resolve()
    return None


frontend_dir = _resolve_frontend_dir()

if frontend_dir and (frontend_dir / "assets").is_dir():
    logger.info("Serving frontend static assets from %s", frontend_dir / "assets")
    app.mount(
        "/assets",
        StaticFiles(directory=str(frontend_dir / "assets")),
        name="frontend-assets",
    )

if frontend_dir and (frontend_dir / "index.html").is_file():
    logger.info("Configured SPA fallback for frontend from %s", frontend_dir)

    def _render_index(index_path: Path, request: Request) -> Response:
        effective_root = (request.scope.get("root_path") or settings.ROOT_PATH or "").rstrip("/")
        if effective_root:
            html = index_path.read_text(encoding="utf-8")
            script = f'<script>window.__DATABRICKS_ROOT_PATH__ = "{effective_root}";</script>'
            if "</head>" in html:
                html = html.replace("</head>", f"{script}</head>", 1)
            html = html.replace('href="/assets/', f'href="{effective_root}/assets/')
            html = html.replace('src="/assets/', f'src="{effective_root}/assets/')
            return HTMLResponse(html)
        return FileResponse(index_path)

    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_frontend(full_path: str, request: Request):
        # Do not catch API or documentation routes
        if full_path.startswith("api/") or full_path == "api" or full_path in ("docs", "redoc", "openapi.json"):
            raise HTTPException(status_code=404, detail="Endpoint not found")

        # 1. Exact static file match (favicon.ico, robots.txt, subpage index.html, etc.)
        target_file = frontend_dir / full_path
        if target_file.is_file():
            if target_file.name == "index.html":
                return _render_index(target_file, request)
            return FileResponse(target_file)

        # 2. Sub-directory with index.html (e.g. /requirements/ -> requirements/index.html)
        if target_file.is_dir():
            sub_index = target_file / "index.html"
            if sub_index.is_file():
                return _render_index(sub_index, request)

        # 3. Fallback to main index.html for client-side routing
        main_index = frontend_dir / "index.html"
        if main_index.is_file():
            return _render_index(main_index, request)

        raise HTTPException(status_code=404, detail="File not found")
else:
    logger.info("Frontend static build not detected. Running in API-only mode.")

    @app.get("/", include_in_schema=False)
    async def root_fallback():
        return {
            "status": "ok",
            "app": settings.APP_NAME,
            "version": settings.APP_VERSION,
            "message": "TraceAudit AI backend API is running. Frontend static build not found in dist/client. Run 'npm run build' to generate the frontend assets.",
            "docs": "/docs",
        }
