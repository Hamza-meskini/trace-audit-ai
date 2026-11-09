"""SEC-07: deleting a document or project erases the customer data derived from it."""
import asyncio
import json
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.documents import router as documents_router
from app.api.projects import router as projects_router
from app.config import settings
from app.database import Base, get_db
from app.models.document import Document, EvidenceChunk
from app.models.project import Project
from app.services import audit_progress, data_purge, databricks_document_ai, embedding, pipeline_cache

SHA_A = "a" * 64
SHA_SHARED = "b" * 64
TEXT_A = "Confidential: the actuator opened in 18 ms at -40 C."
TEXT_SHARED = "Shared boilerplate paragraph present in two documents."


class Env:
    """Temporary upload root, caches and database with two projects."""

    def __init__(self, tmp: Path):
        self.tmp = tmp
        self.uploads = tmp / "uploads"
        self.parse_cache = tmp / "parse-cache"
        self.embed_dir = tmp / "embed"
        for path in (self.uploads, self.parse_cache, self.embed_dir):
            path.mkdir()
        self.volume_calls: list[str] = []

    def patches(self):
        return [
            patch.object(settings, "UPLOAD_DIR", str(self.uploads)),
            patch.object(settings, "DATABRICKS_DOCUMENT_CACHE_DIR", str(self.parse_cache)),
            patch.object(settings, "DATABRICKS_DOCUMENT_VOLUME", "/Volumes/cat/schema/docs"),
            patch.object(databricks_document_ai, "delete_remote_artifacts", self._fake_volume_delete),
            patch.object(embedding, "_CACHE_DIR", self.embed_dir),
            patch.object(embedding, "_CACHE_FILE", self.embed_dir / "embeddings.json"),
            patch.object(embedding, "_JOURNAL_FILE", self.embed_dir / "embeddings.jsonl"),
            patch.object(embedding, "_embedding_cache", {}),
            patch.object(embedding, "_cache_loaded", False),
        ]

    def _fake_volume_delete(self, sha):
        self.volume_calls.append(sha)
        return 3

    def write_original(self, project_id, name):
        folder = self.uploads / project_id
        folder.mkdir(exist_ok=True)
        path = folder / name
        path.write_bytes(b"%PDF-1.4 customer bytes")
        return path

    def write_parse_cache(self, sha):
        for suffix in ("parse-v9-figures", "prep-search-v2"):
            (self.parse_cache / f"{sha}-{suffix}.json").write_text("{}", encoding="utf-8")

    def seed_embeddings(self, texts):
        lines = []
        for text in texts:
            for variant in (text, text[:2048]):
                lines.append(json.dumps({"k": embedding._content_hash(variant, "RETRIEVAL_DOCUMENT"), "v": [0.1, 0.2]}))
        (self.embed_dir / "embeddings.jsonl").write_text("\n".join(lines), encoding="utf-8")

    def embedding_keys_on_disk(self):
        embedding._embedding_cache.clear()
        embedding._cache_loaded = False
        embedding._ensure_cache_loaded()
        return set(embedding._embedding_cache)


@pytest.fixture
def env(tmp_path):
    environment = Env(tmp_path)
    with ExitStack() as stack:
        for item in environment.patches():
            stack.enter_context(item)
        yield environment


def _run(env, scenario):
    async def go():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        a1 = env.write_original("p1", "d1.pdf")
        a2 = env.write_original("p1", "d2.pdf")
        b1 = env.write_original("p2", "d3.pdf")
        async with sessions() as db:
            db.add_all([Project(id="p1", name="P1", product_name="U", audit_id="A1"),
                        Project(id="p2", name="P2", product_name="U", audit_id="A2")])
            db.add_all([
                Document(id="d1", project_id="p1", filename="d1.pdf", original_filename="a.pdf", storage_path=str(a1)),
                Document(id="d2", project_id="p1", filename="d2.pdf", original_filename="b.pdf", storage_path=str(a2)),
                Document(id="d3", project_id="p2", filename="d3.pdf", original_filename="c.pdf", storage_path=str(b1)),
            ])
            db.add_all([
                EvidenceChunk(id="c1", document_id="d1", chunk_index=0, content=TEXT_A,
                              metadata_json={"source_sha256": SHA_A}),
                EvidenceChunk(id="c2", document_id="d1", chunk_index=1, content=TEXT_SHARED,
                              metadata_json={"source_sha256": SHA_A}),
                EvidenceChunk(id="c3", document_id="d2", chunk_index=0, content="Doc two text.",
                              metadata_json={"source_sha256": SHA_SHARED}),
                # Project 2 holds a copy of d2's file and d1's shared paragraph.
                EvidenceChunk(id="c4", document_id="d3", chunk_index=0, content=TEXT_SHARED,
                              metadata_json={"source_sha256": SHA_SHARED}),
            ])
            await db.commit()
        env.write_parse_cache(SHA_A)
        env.write_parse_cache(SHA_SHARED)
        env.seed_embeddings([TEXT_A, TEXT_SHARED, "Doc two text."])
        pipeline_cache.set_json("custom-reranker", "k1", {"items": [{"key": "c1", "score": 1.0}]})
        pipeline_cache.set_json("custom-reranker", "k2", {"items": [{"key": "c4", "score": 1.0}]})

        app = FastAPI()
        app.include_router(projects_router, prefix="/api")
        app.include_router(documents_router, prefix="/api")

        async def dep():
            async with sessions() as db:
                yield db
                await db.commit()
        app.dependency_overrides[get_db] = dep
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client:
                return await scenario(client, sessions)
        finally:
            await engine.dispose()
    return asyncio.run(go())


def test_document_delete_erases_original_caches_vectors_and_volume_images(env):
    async def scenario(client, sessions):
        response = await client.delete("/api/projects/p1/documents/d1")
        assert response.status_code == 204
        async with sessions() as db:
            assert (await db.execute(select(EvidenceChunk).where(EvidenceChunk.document_id == "d1"))).first() is None
        return None

    _run(env, scenario)
    assert not (env.uploads / "p1" / "d1.pdf").exists()
    assert (env.uploads / "p1" / "d2.pdf").exists()
    assert not list(env.parse_cache.glob(f"{SHA_A}-*.json"))
    assert len(list(env.parse_cache.glob(f"{SHA_SHARED}-*.json"))) == 2
    assert env.volume_calls == [SHA_A]
    keys = env.embedding_keys_on_disk()
    assert embedding._content_hash(TEXT_A, "RETRIEVAL_DOCUMENT") not in keys
    # Still used by project 2, so kept.
    assert embedding._content_hash(TEXT_SHARED, "RETRIEVAL_DOCUMENT") in keys
    assert pipeline_cache.get_json("custom-reranker", "k1") is None
    assert pipeline_cache.get_json("custom-reranker", "k2") is not None


def test_project_delete_erases_folder_progress_and_unshared_caches(env):
    async def scenario(client, sessions):
        audit_progress.start("p1", "m")
        audit_progress.update("p1", "completed")
        with patch.object(audit_progress, "latest", return_value={"status": "completed"}):
            response = await client.delete("/api/projects/p1")
        assert response.status_code == 204
        async with sessions() as db:
            remaining = (await db.execute(select(Document.id))).scalars().all()
        return remaining

    remaining = _run(env, scenario)
    assert remaining == ["d3"]
    assert not (env.uploads / "p1").exists()
    assert (env.uploads / "p2" / "d3.pdf").exists()
    assert audit_progress.latest("p1") is None
    assert not list(env.parse_cache.glob(f"{SHA_A}-*.json"))
    # d2's file hash is still used by project 2's copy.
    assert len(list(env.parse_cache.glob(f"{SHA_SHARED}-*.json"))) == 2
    assert env.volume_calls == [SHA_A]
    keys = env.embedding_keys_on_disk()
    assert embedding._content_hash("Doc two text.", "RETRIEVAL_DOCUMENT") not in keys
    assert embedding._content_hash(TEXT_SHARED, "RETRIEVAL_DOCUMENT") in keys


def test_purge_never_deletes_outside_the_upload_root(env, tmp_path):
    outside = tmp_path / "outside.pdf"
    outside.write_bytes(b"x")
    assert data_purge._delete_originals([str(outside)]) == 0
    assert outside.exists()
    assert data_purge._delete_project_dir("..") is False
    assert data_purge._delete_project_dir("") is False


def test_volume_failure_is_counted_not_raised(env):
    with patch.object(databricks_document_ai, "delete_remote_artifacts", side_effect=RuntimeError("403")):
        assert data_purge._delete_remote_images({SHA_A}) == (0, 1)


def test_delete_remote_artifacts_walks_the_image_folder():
    root = f"/Volumes/cat/schema/docs/auditrace-document-ai-images/{SHA_A}"
    listing = {
        root: [SimpleNamespace(path=f"{root}/p1.png", is_directory=False),
               SimpleNamespace(path=f"{root}/sub", is_directory=True)],
        f"{root}/sub": [SimpleNamespace(path=f"{root}/sub/p2.png", is_directory=False)],
    }
    files = MagicMock()
    files.list_directory_contents.side_effect = lambda path: listing[path]
    workspace = MagicMock(files=files)
    with patch.object(databricks_document_ai, "_databricks_clients", return_value=(None, MagicMock(return_value=workspace))), \
            patch.object(databricks_document_ai, "_document_ai_config",
                         return_value=("h", "t", "w", "/Volumes/cat/schema/docs")):
        assert databricks_document_ai.delete_remote_artifacts(SHA_A) == 2
    deleted_dirs = [c.args[0] for c in files.delete_directory.call_args_list]
    assert deleted_dirs == [f"{root}/sub", root]
    with pytest.raises(ValueError):
        databricks_document_ai.delete_remote_artifacts("../etc")
