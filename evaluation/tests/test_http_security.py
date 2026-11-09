"""SEC-11: CORS without credentials, security headers, cross-site write guard, upload caps."""
import asyncio

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.services.http_security import SecurityMiddleware, origin_allowed

DEV = ["http://localhost:5173"]


def _app(max_body_bytes=1024):
    app = FastAPI()

    @app.get("/api/ping")
    async def ping():
        return {"ok": True}

    @app.post("/api/write")
    async def write():
        return {"ok": True}

    app.add_middleware(CORSMiddleware, allow_origins=DEV, allow_credentials=False,
                       allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
                       allow_headers=["Content-Type", "Accept"])
    app.add_middleware(SecurityMiddleware, allowed_origins=DEV, max_body_bytes=max_body_bytes)
    return app


def _request(method, path, **kwargs):
    async def go():
        transport = httpx.ASGITransport(app=_app())
        async with httpx.AsyncClient(transport=transport, base_url="http://app.example") as client:
            return await client.request(method, path, **kwargs)
    return asyncio.run(go())


def test_security_headers_on_every_response():
    response = _request("GET", "/api/ping")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "SAMEORIGIN"
    assert response.headers["referrer-policy"] == "same-origin"


def test_cross_site_form_post_is_blocked():
    response = _request("POST", "/api/write", headers={"Origin": "https://evil.example", "Sec-Fetch-Site": "cross-site"})
    assert response.status_code == 403
    assert response.headers["x-content-type-options"] == "nosniff"


def test_same_host_dev_origin_and_originless_writes_pass():
    assert _request("POST", "/api/write", headers={"Origin": "http://app.example"}).status_code == 200
    assert _request("POST", "/api/write", headers={"Origin": "http://localhost:5173"}).status_code == 200
    assert _request("POST", "/api/write").status_code == 200
    assert _request("POST", "/api/write", headers={"Origin": "https://x.example", "Sec-Fetch-Site": "same-origin"}).status_code == 200


def test_proxy_forwarded_host_counts_as_same_origin():
    assert origin_allowed("https://traceaudit-123.aws.databricksapps.com", host="127.0.0.1:8000",
                          forwarded_host="traceaudit-123.aws.databricksapps.com", allowed_origins=[])
    assert not origin_allowed("null", host="a", forwarded_host="", allowed_origins=[])


def test_cors_does_not_allow_credentials():
    response = _request("OPTIONS", "/api/write", headers={
        "Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type"})
    assert response.status_code == 200
    assert response.headers.get("access-control-allow-credentials") is None
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_oversized_request_rejected_before_parsing():
    response = _request("POST", "/api/write", content=b"x" * 2048, headers={"Content-Type": "application/octet-stream"})
    assert response.status_code == 413


def test_main_app_wires_the_middleware():
    from app.main import app

    classes = [m.cls for m in app.user_middleware]
    assert SecurityMiddleware in classes and CORSMiddleware in classes
    assert classes.index(SecurityMiddleware) < classes.index(CORSMiddleware), "security must be outermost"
    cors = next(m for m in app.user_middleware if m.cls is CORSMiddleware)
    assert cors.kwargs["allow_credentials"] is False


def test_batch_upload_rejects_too_many_files(tmp_path):
    from unittest.mock import patch

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.api.documents import router as documents_router
    from app.config import settings
    from app.database import Base, get_db
    from app.models.project import Project

    async def go():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as db:
            db.add(Project(id="p1", name="P", product_name="U", audit_id="A1"))
            await db.commit()
        app = FastAPI()
        app.include_router(documents_router, prefix="/api")

        async def dep():
            async with sessions() as db:
                yield db
                await db.commit()
        app.dependency_overrides[get_db] = dep
        files = [("files", (f"f{i}.csv", b"a,b\n1,2\n", "text/csv")) for i in range(3)]
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client:
                return await client.post("/api/projects/p1/documents/batch", files=files)
        finally:
            await engine.dispose()

    with patch.object(settings, "UPLOAD_DIR", str(tmp_path)), \
            patch.object(settings, "MAX_UPLOAD_FILES_PER_REQUEST", 2):
        response = asyncio.run(go())
    assert response.status_code == 413
    assert not any(tmp_path.rglob("*.csv")), "rejected uploads must not leave files behind"
