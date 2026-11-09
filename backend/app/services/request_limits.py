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


# REL-06: one process-wide view of each provider/model's capacity. A 429 with
# Retry-After pauses every caller of that model, not only the one that was
# refused, so the stages stop competing into more 429s. Optional pacing
# (LLM_REQUESTS_PER_MINUTE, off by default) spaces requests evenly.
_cooldown_until: dict[str, float] = {}
_next_slot: dict[str, float] = {}


def provider_key(provider: str, model: str | None) -> str:
    return f"{provider}:{(model or '').strip().lower()}"


def note_rate_limited(key: str, delay_seconds: float) -> None:
    """Hold every request for ``key`` until the announced delay has passed."""
    until = time.monotonic() + max(0.0, float(delay_seconds))
    if until > _cooldown_until.get(key, 0.0):
        _cooldown_until[key] = until


def cooldown_remaining(key: str) -> float:
    return max(0.0, _cooldown_until.get(key, 0.0) - time.monotonic())


async def wait_for_provider(key: str) -> float:
    """Wait out any shared cooldown and pacing for ``key``; return seconds waited."""
    waited = 0.0
    waited_for = None
    # Sleep once per deadline; repeat only if another caller extended it meanwhile.
    while (deadline := _cooldown_until.get(key, 0.0)) != waited_for:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        waited_for = deadline
        await asyncio.sleep(remaining)
        waited += remaining
    per_minute = int(getattr(settings, "LLM_REQUESTS_PER_MINUTE", 0) or 0)
    if per_minute > 0:
        now = time.monotonic()
        slot = max(now, _next_slot.get(key, 0.0))
        _next_slot[key] = slot + 60.0 / per_minute
        if slot > now:
            await asyncio.sleep(slot - now)
            waited += slot - now
    return waited


def reset_provider_limits() -> None:
    _cooldown_until.clear()
    _next_slot.clear()


@asynccontextmanager
async def outbound_slot(pool: str = "llm"):
    """Hold one process-wide slot for a provider request."""
    semaphore = _semaphore(pool)
    started = time.perf_counter()
    async with semaphore:
        yield time.perf_counter() - started
