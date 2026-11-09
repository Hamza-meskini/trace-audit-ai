"""TraceAudit AI — FastAPI application entry point."""

import logging
import json
from html import escape
from contextlib import asynccontextmanager

from pathlib import Path
from urllib.parse import unquote
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse

from app.config import settings
from app.services.log_redaction import install_log_redaction
from app.services.http_security import SecurityMiddleware
from app.services.identity import request_identity

install_log_redaction()

from app.database import init_db, get_db_context
from app.api.settings import load_persisted_settings

from app.api.projects import router as projects_router
from app.api.documents import router as documents_router
from app.api.requirements import router as requirements_router
from app.api.findings import router as findings_router
from app.api.audit import router as audit_router
from app.api.settings import router as settings_router
from app.api.visitors import router as visitors_router

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

    async with get_db_context() as db:
        await load_persisted_settings(db)

    from app.services.verification_reasoner import adjudicator_configuration_warning
    if (adjudicator_warning := adjudicator_configuration_warning()):
        logger.warning(adjudicator_warning)
    from app.services.egress import log_startup_report
    log_startup_report()
    from app.services.identity import forwarded_identity_trusted
    if forwarded_identity_trusted() and not Path(settings.UPLOAD_DIR).is_absolute():
        # SEC-04: originals on a relative path live on the app's local disk;
        # their survival across restarts is unverified (DEPLOYMENT.md).
        logger.warning("UPLOAD_DIR is relative (%s); uploaded originals may not survive an app restart",
                       settings.UPLOAD_DIR)

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

# CORS — only the local development frontend calls the API cross-origin
# (Vite on 5173/8080/3000 -> uvicorn on 8000); the deployed app is
# same-origin. The frontend never sends credentials, so none are allowed.
_cors_origins = settings.CORS_ORIGINS or ["http://localhost:5173", "http://localhost:8080", "http://localhost:3000"]
_cors_origin_regex = r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$" if settings.DEBUG else None
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_origin_regex=_cors_origin_regex,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Content-Type", "Accept"],
)
# Added last, so it runs first: security headers, cross-site write guard and a
# request-size cap ahead of routing and multipart parsing.
app.add_middleware(
    SecurityMiddleware,
    allowed_origins=_cors_origins,
    allowed_origin_regex=_cors_origin_regex,
    max_body_bytes=settings.MAX_REQUEST_BODY_MB * 1024 * 1024,
)

# Register API routers
app.include_router(projects_router, prefix="/api")
app.include_router(documents_router, prefix="/api")
app.include_router(requirements_router, prefix="/api")
app.include_router(findings_router, prefix="/api")
app.include_router(audit_router, prefix="/api")
app.include_router(settings_router, prefix="/api")
app.include_router(visitors_router, prefix="/api")


@app.get("/api/health")
async def health_check():
    return {"status": "ok", "version": settings.APP_VERSION}


@app.get("/api/me")
async def current_user(request: Request):
    """Expose the SSO identity when the Databricks Apps proxy vouches for it."""
    identity = request_identity(request)
    if identity.verified:
        return {"user": identity.user, "username": identity.display, "email": identity.email or identity.user,
                "authenticated": True, "provider": "databricks_sso"}
    return {"user": "Local reviewer", "username": "Local reviewer", "email": None,
            "authenticated": False, "provider": "local"}


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
        html = index_path.read_text(encoding="utf-8")
        safe_root = escape(effective_root, quote=True)
        # Relative CSS URLs must resolve from the app root on deep-link reloads.
        html = html.replace("<head>", f'<head><base href="{safe_root}/">', 1)
        if effective_root:
            root_json = json.dumps(effective_root).replace("<", "\\u003c")
            script = f'<script>window.__DATABRICKS_ROOT_PATH__ = {root_json};</script>'
            if "</head>" in html:
                html = html.replace("</head>", f"{script}</head>", 1)
            # Include preload URLs and TanStack's serialized script manifest.
            for prefix in ('"/assets/', '"/./assets/'):
                html = html.replace(prefix, f'"{safe_root}/assets/')
        return HTMLResponse(html, headers={"Cache-Control": "no-cache"})

    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_frontend(full_path: str, request: Request):
        # Do not catch API or documentation routes
        if full_path.startswith("api/") or full_path == "api" or full_path in ("docs", "redoc", "openapi.json"):
            raise HTTPException(status_code=404, detail="Endpoint not found")

        # Reject encoded and plain parent-directory segments before the SPA fallback.
        decoded = unquote(full_path).replace("\\", "/")
        if ".." in [part for part in decoded.split("/") if part not in ("", ".")]:
            raise HTTPException(status_code=404, detail="File not found")

        # 1. Exact static file match (favicon.ico, robots.txt, subpage index.html, etc.)
        target_file = (frontend_dir / full_path).resolve()
        if not target_file.is_relative_to(frontend_dir):
            raise HTTPException(status_code=404, detail="File not found")
        if target_file.is_file():
            if target_file.name == "index.html":
                return _render_index(target_file, request)
            return FileResponse(target_file)

        # 2. Sub-directory with index.html (e.g. /requirements/ -> requirements/index.html)
        if target_file.is_dir():
            sub_index = (target_file / "index.html").resolve()
            if sub_index.is_relative_to(frontend_dir) and sub_index.is_file():
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
