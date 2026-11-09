"""Erase customer data derived from deleted documents and projects.

Deleting a document or project removes its database rows (documents, evidence
chunks, requirement links). This module erases everything else derived from
the same customer content, after the database commit succeeds:

* the uploaded original under ``UPLOAD_DIR`` (and, for a project, its folder);
* the local Databricks parse / prep-search cache files, keyed by the file's
  SHA-256;
* parsed figure images in the Databricks Volume
  (``<volume>/auditrace-document-ai-images/<sha256>``);
* embedding vectors cached for the deleted chunks' text;
* custom-reranker cache entries that reference the deleted chunks;
* the project's audit-progress record.

Shared content is reference counted: a cache entry keyed by a file hash or a
chunk text is kept while another remaining document still has that hash or
text. Failures are logged and counted, never raised -- the rows are already
gone and the request has succeeded.

``purge_inventory`` returns counts only (no names or text), which the
endpoints log as the deletion record.
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

from sqlalchemy import select
from starlette.concurrency import run_in_threadpool

from app.config import settings
from app.models.document import Document, EvidenceChunk

logger = logging.getLogger("traceaudit.purge")


@dataclass
class PurgePlan:
    document_ids: list[str] = field(default_factory=list)
    storage_paths: list[str] = field(default_factory=list)
    source_sha256: set[str] = field(default_factory=set)
    chunk_ids: list[str] = field(default_factory=list)
    chunk_texts: list[str] = field(default_factory=list)
    project_id: Optional[str] = None


def _upload_root() -> Path:
    return Path(settings.UPLOAD_DIR).resolve()


def _inside_upload_root(path: Path) -> bool:
    try:
        return path.resolve().is_relative_to(_upload_root())
    except (OSError, ValueError):
        return False


async def plan_purge(db, document_ids: Iterable[str], project_id: Optional[str] = None) -> PurgePlan:
    """Collect what to erase. Call before deleting the rows."""
    ids = list(dict.fromkeys(document_ids))
    plan = PurgePlan(document_ids=ids, project_id=project_id)
    if not ids:
        return plan
    documents = (await db.execute(select(Document).where(Document.id.in_(ids)))).scalars().all()
    plan.storage_paths = [doc.storage_path for doc in documents if doc.storage_path]
    rows = (await db.execute(
        select(EvidenceChunk.id, EvidenceChunk.content, EvidenceChunk.metadata_json)
        .where(EvidenceChunk.document_id.in_(ids))
    )).all()
    for chunk_id, content, metadata in rows:
        plan.chunk_ids.append(chunk_id)
        if content:
            plan.chunk_texts.append(content)
        sha = (metadata or {}).get("source_sha256")
        if sha:
            plan.source_sha256.add(str(sha))
    if not plan.source_sha256:
        from app.services.ingestion import file_sha256

        for path in plan.storage_paths:
            try:
                plan.source_sha256.add(await run_in_threadpool(file_sha256, path))
            except OSError:
                pass
    return plan


async def _remaining_hashes(db, hashes: set[str]) -> set[str]:
    """Hashes still used by a remaining document (one chunk per document)."""
    if not hashes:
        return set()
    rows = (await db.execute(
        select(EvidenceChunk.metadata_json).where(EvidenceChunk.chunk_index == 0)
    )).scalars().all()
    return {str((m or {}).get("source_sha256")) for m in rows} & hashes


async def _remaining_texts(db, texts: list[str]) -> set[str]:
    if not texts:
        return set()
    unique = list(dict.fromkeys(texts))
    kept: set[str] = set()
    for start in range(0, len(unique), 50):
        batch = unique[start:start + 50]
        kept.update((await db.execute(
            select(EvidenceChunk.content).where(EvidenceChunk.content.in_(batch))
        )).scalars().all())
    return kept


def _delete_originals(paths: list[str]) -> int:
    deleted = 0
    for raw in paths:
        path = Path(raw)
        if not _inside_upload_root(path):
            logger.warning("Not deleting an original outside UPLOAD_DIR")
            continue
        try:
            path.unlink()
            deleted += 1
        except FileNotFoundError:
            pass
        except OSError as exc:
            logger.warning("Could not delete an uploaded original: %s", type(exc).__name__)
    return deleted


def _delete_parse_cache(hashes: set[str]) -> int:
    from app.services.databricks_document_ai import _cache_path

    deleted = 0
    for sha in hashes:
        root = _cache_path(sha).parent
        if not root.is_dir():
            continue
        for path in root.glob(f"{sha}-*.json"):
            try:
                path.unlink()
                deleted += 1
            except OSError as exc:
                logger.warning("Could not delete a parse cache file: %s", type(exc).__name__)
    return deleted


def _delete_remote_images(hashes: set[str]) -> tuple[int, int]:
    if not hashes or not settings.DATABRICKS_DOCUMENT_VOLUME:
        return 0, 0
    from app.services.databricks_document_ai import delete_remote_artifacts

    deleted = errors = 0
    for sha in hashes:
        try:
            deleted += delete_remote_artifacts(sha)
        except Exception as exc:  # network, permissions, missing SDK
            errors += 1
            logger.warning("Could not delete Databricks Volume images for a document: %s", type(exc).__name__)
    return deleted, errors


def _delete_project_dir(project_id: Optional[str]) -> bool:
    if not project_id:
        return False
    root = _upload_root()
    target = (root / project_id).resolve()
    if target == root or not target.is_relative_to(root):
        logger.warning("Refusing to remove a project folder outside UPLOAD_DIR")
        return False
    if not target.is_dir():
        return False
    shutil.rmtree(target, ignore_errors=True)
    return not target.exists()


def _forget_progress(project_id: Optional[str]) -> None:
    if not project_id:
        return
    from app.services import audit_progress

    try:
        audit_progress.forget(project_id)
    except Exception as exc:
        logger.warning("Could not delete the audit progress record: %s", type(exc).__name__)


async def execute_purge(db, plan: PurgePlan) -> dict[str, Any]:
    """Erase derived data for a committed deletion and return counts."""
    from app.services import embedding, pipeline_cache

    shared_hashes = await _remaining_hashes(db, plan.source_sha256)
    hashes = plan.source_sha256 - shared_hashes
    shared_texts = await _remaining_texts(db, plan.chunk_texts)
    texts = [text for text in plan.chunk_texts if text not in shared_texts]

    inventory: dict[str, Any] = {
        "documents": len(plan.document_ids),
        "chunks": len(plan.chunk_ids),
        "originals_deleted": await run_in_threadpool(_delete_originals, plan.storage_paths),
        "parse_cache_files_deleted": await run_in_threadpool(_delete_parse_cache, hashes),
        "shared_sources_kept": len(shared_hashes),
        "shared_chunk_texts_kept": len(set(shared_texts)),
    }
    volume_deleted, volume_errors = await run_in_threadpool(_delete_remote_images, hashes)
    inventory["volume_files_deleted"] = volume_deleted
    inventory["volume_errors"] = volume_errors
    inventory["embedding_vectors_deleted"] = await run_in_threadpool(embedding.forget_texts, texts)
    inventory["reranker_cache_entries_deleted"] = await run_in_threadpool(
        pipeline_cache.delete_referencing, "custom-reranker", plan.chunk_ids
    )
    inventory["project_folder_removed"] = await run_in_threadpool(_delete_project_dir, plan.project_id)
    await run_in_threadpool(_forget_progress, plan.project_id)
    logger.info("Deletion purge: %s", inventory)
    return inventory
