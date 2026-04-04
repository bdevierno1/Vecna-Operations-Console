"""Pre/post tool lifecycle hooks for recon tools (before/after each tool call)."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from app.services.operation_context import make_tool_event_payload, schedule_tool_emit
from app.services.telemetry import log_tool_lifecycle


def run_tool_with_hooks(tool_name: str, fn: Callable[..., str], /, *args: Any, **kwargs: Any) -> str:
    """Run a sync tool with pre/post hooks, hub events, and telemetry."""
    schedule_tool_emit(make_tool_event_payload(tool=tool_name, phase="start"))
    log_tool_lifecycle(tool=tool_name, phase="start")
    t0 = time.monotonic()
    try:
        out = fn(*args, **kwargs)
        dt_ms = (time.monotonic() - t0) * 1000.0
        schedule_tool_emit(
            make_tool_event_payload(tool=tool_name, phase="end", duration_ms=dt_ms, ok=True),
        )
        log_tool_lifecycle(tool=tool_name, phase="end", ok=True, duration_ms=dt_ms)
        return out
    except Exception as e:
        dt_ms = (time.monotonic() - t0) * 1000.0
        err = str(e)
        schedule_tool_emit(
            make_tool_event_payload(tool=tool_name, phase="end", duration_ms=dt_ms, ok=False, error=err),
        )
        log_tool_lifecycle(tool=tool_name, phase="end", ok=False, duration_ms=dt_ms, error=err)
        raise
