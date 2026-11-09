"""Visitor leads: public capture removed (SEC-05); stored leads can be listed, exported, erased."""

import sys
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent.parent / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import httpx
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.visitors import router as visitors_router
from app.database import Base, get_db
from app.models.visitor import Visitor

REPO = BACKEND_DIR.parent


class VisitorLeadTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

        app = FastAPI()
        app.include_router(visitors_router, prefix="/api")

        async def session_dependency():
            async with self.sessions() as db:
                yield db
                await db.commit()

        app.dependency_overrides[get_db] = session_dependency
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.engine.dispose()

    async def _seed(self, *leads):
        async with self.sessions() as db:
            rows = [Visitor(**lead) for lead in leads]
            db.add_all(rows)
            await db.commit()
            return [row.id for row in rows]

    async def test_public_capture_endpoint_is_gone(self):
        res = await self.client.post("/api/visitors", json={"email": "lead@example.test"})
        self.assertEqual(res.status_code, 405)

    async def test_list_and_search_visitors(self):
        await self._seed(
            {"email": "alice@bosch.de", "full_name": "Alice M", "company": "Bosch"},
            {"email": "bob@continental.com", "full_name": "Bob K", "company": "Continental"},
            {"email": "clara@valeo.fr", "full_name": "Clara T", "company": "Valeo"},
        )
        res = await self.client.get("/api/visitors")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["total"], 3)

        search_res = await self.client.get("/api/visitors?search=Continental")
        self.assertEqual(search_res.status_code, 200)
        items = search_res.json()["items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["email"], "bob@continental.com")

    async def test_export_visitors_csv(self):
        await self._seed({"email": "export.test@zf.com", "full_name": "ZF Tester",
                          "company": "ZF Group", "role": "Safety Lead"})
        res = await self.client.get("/api/visitors/export")
        self.assertEqual(res.status_code, 200)
        self.assertIn("text/csv", res.headers.get("content-type", ""))
        self.assertIn("attachment; filename=", res.headers.get("content-disposition", ""))
        body = res.text
        self.assertIn("ID,Email,Full Name,Company,Role", body)
        self.assertIn("export.test@zf.com", body)
        self.assertIn("ZF Group", body)

    async def test_delete_visitor(self):
        (vid,) = await self._seed({"email": "to_delete@temp.com"})
        del_res = await self.client.delete(f"/api/visitors/{vid}")
        self.assertEqual(del_res.status_code, 200)
        del_again = await self.client.delete(f"/api/visitors/{vid}")
        self.assertEqual(del_again.status_code, 404)


def test_frontend_has_no_visitor_capture_caller():
    """Removal was conditional on nothing in src/ posting leads."""
    sources = [p for p in (REPO / "src").rglob("*") if p.suffix in {".ts", ".tsx"}]
    text = "\n".join(p.read_text(encoding="utf-8") for p in sources)
    for marker in ("registerVisitor", "useRegisterVisitor", "VisitorCaptureModal", "RegisterVisitorRequest"):
        assert marker not in text, marker


def test_purge_script_requires_explicit_confirmation():
    import importlib.util

    import pytest

    spec = importlib.util.spec_from_file_location("purge_visitor_leads", BACKEND_DIR / "scripts" / "purge_visitor_leads.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with pytest.raises(SystemExit):
        module.main(["--delete"])
