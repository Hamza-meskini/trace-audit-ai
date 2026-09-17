"""Unit tests for visitor lead capture, deduplication, search, and CSV export."""

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


class VisitorCaptureTests(unittest.IsolatedAsyncioTestCase):
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

    async def test_register_visitor_success(self):
        payload = {
            "email": "lead@automotive-oem.com",
            "full_name": "Elena Rostova",
            "company": "NextGen Mobility",
            "role": "Compliance Engineer",
            "source": "demo_modal",
            "notes": "Interested in ISO 26262 matrix audits",
        }
        res = await self.client.post("/api/visitors", json=payload)
        self.assertEqual(res.status_code, 201)
        data = res.json()
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["lead"]["email"], "lead@automotive-oem.com")
        self.assertEqual(data["lead"]["full_name"], "Elena Rostova")
        self.assertEqual(data["lead"]["company"], "NextGen Mobility")
        self.assertEqual(data["lead"]["role"], "Compliance Engineer")
        self.assertTrue(data["lead"]["id"])

    async def test_register_visitor_invalid_email(self):
        payload = {
            "email": "not-a-valid-email",
            "full_name": "Bad Email User",
        }
        res = await self.client.post("/api/visitors", json=payload)
        self.assertEqual(res.status_code, 422)

    async def test_register_visitor_duplicate_updates_gracefully(self):
        payload1 = {
            "email": "engineer@aerospace.org",
            "full_name": "John Doe",
            "company": "Initial Aero",
        }
        res1 = await self.client.post("/api/visitors", json=payload1)
        self.assertEqual(res1.status_code, 201)
        initial_id = res1.json()["lead"]["id"]

        payload2 = {
            "email": "ENGINEER@aerospace.org",  # case insensitive
            "full_name": "John Doe Updated",
            "company": "Aero Global Inc",
            "role": "Lead Systems Auditor",
        }
        res2 = await self.client.post("/api/visitors", json=payload2)
        self.assertEqual(res2.status_code, 201)
        data2 = res2.json()
        self.assertEqual(data2["lead"]["id"], initial_id)
        self.assertEqual(data2["lead"]["full_name"], "John Doe Updated")
        self.assertEqual(data2["lead"]["company"], "Aero Global Inc")

        # Verify only 1 record exists in list
        list_res = await self.client.get("/api/visitors")
        self.assertEqual(list_res.status_code, 200)
        self.assertEqual(list_res.json()["total"], 1)

    async def test_list_and_search_visitors(self):
        users = [
            {"email": "alice@bosch.de", "full_name": "Alice M", "company": "Bosch"},
            {"email": "bob@continental.com", "full_name": "Bob K", "company": "Continental"},
            {"email": "clara@valeo.fr", "full_name": "Clara T", "company": "Valeo"},
        ]
        for u in users:
            await self.client.post("/api/visitors", json=u)

        # Full list
        res = await self.client.get("/api/visitors")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["total"], 3)

        # Search by company
        search_res = await self.client.get("/api/visitors?search=Continental")
        self.assertEqual(search_res.status_code, 200)
        items = search_res.json()["items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["email"], "bob@continental.com")

    async def test_export_visitors_csv(self):
        await self.client.post(
            "/api/visitors",
            json={
                "email": "export.test@zf.com",
                "full_name": "ZF Tester",
                "company": "ZF Group",
                "role": "Safety Lead",
            },
        )
        res = await self.client.get("/api/visitors/export")
        self.assertEqual(res.status_code, 200)
        self.assertIn("text/csv", res.headers.get("content-type", ""))
        self.assertIn("attachment; filename=", res.headers.get("content-disposition", ""))
        body = res.text
        self.assertIn("ID,Email,Full Name,Company,Role", body)
        self.assertIn("export.test@zf.com", body)
        self.assertIn("ZF Group", body)

    async def test_delete_visitor(self):
        res = await self.client.post("/api/visitors", json={"email": "to_delete@temp.com"})
        vid = res.json()["lead"]["id"]

        del_res = await self.client.delete(f"/api/visitors/{vid}")
        self.assertEqual(del_res.status_code, 200)

        # Deleting again should be 404
        del_again = await self.client.delete(f"/api/visitors/{vid}")
        self.assertEqual(del_again.status_code, 404)
