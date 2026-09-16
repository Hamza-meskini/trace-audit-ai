"""Shared, bounded capacity for expensive outbound audit operations."""

from __future__ import annotations

import asyncio
import time
import weakref
from contextlib import asynccontextmanager

from app.config import settings


_semaphores: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, dict[str, asyncio.Semaphore]]" = (
    weakref.WeakKeyDictionary()
)


def _limit_for(pool: str) -> int:
    if pool == "reranker":
        return max(1, int(settings.AUDIT_RERANKER_CONCURRENCY))
    return max(1, int(settings.LLM_GLOBAL_CONCURRENCY))


def _semaphore(pool: str) -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    pools = _semaphores.setdefault(loop, {})
    configured = _limit_for(pool)
    semaphore = pools.get(pool)
    if semaphore is None:
        semaphore = asyncio.Semaphore(configured)
        pools[pool] = semaphore
    return semaphore


@asynccontextmanager
async def outbound_slot(pool: str = "llm"):
    """Hold one process-wide slot for a provider request."""
    semaphore = _semaphore(pool)
    started = time.perf_counter()
    async with semaphore:
        yield time.perf_counter() - started
