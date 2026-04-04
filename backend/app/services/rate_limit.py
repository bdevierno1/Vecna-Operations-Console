"""Per-IP sliding-window rate limiter for the operations API.

Goal: return a clear, user-facing "you're limited" message instead of opaque
failures when too many operations are started from one IP.

Design: fixed-window counter keyed by client IP, stored in-process.
Each window rolls over after WINDOW_SECONDS.  Limits are conservative
by default; override via env vars VECNA_OPS_PER_MINUTE / VECNA_OPS_PER_HOUR.

Usage:
    from app.services.rate_limit import ops_rate_limit
    @app.post("/api/operations", dependencies=[Depends(ops_rate_limit)])
"""

from __future__ import annotations

import os
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Deque

from fastapi import HTTPException, Request


# ---------------------------------------------------------------------------
# Config (env-override friendly)
# ---------------------------------------------------------------------------
# Ops per IP per 60s window
_OPS_PER_MINUTE: int = int(os.environ.get("VECNA_OPS_PER_MINUTE", "10"))
# Ops per IP per 3600s window  (hourly budget)
_OPS_PER_HOUR: int = int(os.environ.get("VECNA_OPS_PER_HOUR", "60"))


# ---------------------------------------------------------------------------
# Sliding-window counter (in-memory, process-local)
# ---------------------------------------------------------------------------

@dataclass
class _WindowCounter:
    timestamps: Deque[float] = field(default_factory=deque)


_minute_counters: dict[str, _WindowCounter] = defaultdict(_WindowCounter)
_hour_counters:   dict[str, _WindowCounter] = defaultdict(_WindowCounter)


def _count_in_window(counter: _WindowCounter, window_secs: float, now: float) -> int:
    cutoff = now - window_secs
    while counter.timestamps and counter.timestamps[0] < cutoff:
        counter.timestamps.popleft()
    return len(counter.timestamps)


def _record(counter: _WindowCounter, now: float) -> None:
    counter.timestamps.append(now)


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return "unknown"


# ---------------------------------------------------------------------------
# FastAPI dependency
# ---------------------------------------------------------------------------

async def ops_rate_limit(request: Request) -> None:
    """Raise HTTP 429 if this IP has exceeded the per-minute or per-hour limit."""
    ip = _client_ip(request)
    now = time.monotonic()

    minute_count = _count_in_window(_minute_counters[ip], 60.0, now)
    if minute_count >= _OPS_PER_MINUTE:
        raise HTTPException(
            status_code=429,
            detail=(
                f"Rate limit reached: max {_OPS_PER_MINUTE} operations per minute per IP. "
                "Wait a moment and try again."
            ),
            headers={"Retry-After": "60"},
        )

    hour_count = _count_in_window(_hour_counters[ip], 3600.0, now)
    if hour_count >= _OPS_PER_HOUR:
        raise HTTPException(
            status_code=429,
            detail=(
                f"Rate limit reached: max {_OPS_PER_HOUR} operations per hour per IP. "
                "Check back later."
            ),
            headers={"Retry-After": "3600"},
        )

    _record(_minute_counters[ip], now)
    _record(_hour_counters[ip], now)


def current_limits() -> dict:
    """Return the configured limits (for /api/health/deep and /api/config)."""
    return {
        "ops_per_minute_per_ip": _OPS_PER_MINUTE,
        "ops_per_hour_per_ip": _OPS_PER_HOUR,
    }
