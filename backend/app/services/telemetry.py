"""Structured logging for billing and operations (server-side)."""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger("vecna.telemetry")


def log_event(event: str, payload: dict[str, Any]) -> None:
    """Emit one JSON line per event for log aggregators (Datadog, CloudWatch, etc.)."""
    line = json.dumps({"event": event, **payload}, default=str)
    logger.info("%s", line)


def log_llm_query(
    *,
    operation_id: str,
    model_id: str,
    gateway: str | None = None,
) -> None:
    """Log once per operation start (model + optional gateway)."""
    log_event(
        "llm_query",
        {
            "operation_id": operation_id,
            "model_id": model_id,
            **({"gateway": gateway} if gateway else {}),
        },
    )


def log_operation_completed(
    *,
    operation_id: str,
    session_id: str | None,
    model_id: str,
    gateway: str | None = None,
    cost_usd: float,
    billable_total_usd: float,
    tool_units: int,
    input_tokens: int,
    output_tokens: int,
    wall_seconds: float,
    cost_estimate_unknown: bool = False,
) -> None:
    log_event(
        "operation_completed",
        {
            "operation_id": operation_id,
            "session_id": session_id,
            "model_id": model_id,
            **({"gateway": gateway} if gateway else {}),
            "cost_usd": cost_usd,
            "billable_total_usd": billable_total_usd,
            "tool_units": tool_units,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "wall_seconds": round(wall_seconds, 3),
            "cost_estimate_unknown": cost_estimate_unknown,
        },
    )


def log_unknown_model_cost(*, model_id: str) -> None:
    """Pricing fell back to default tier (unknown model id)."""
    log_event("unknown_model_cost", {"model_id": model_id})


def log_agent_retry(
    *,
    operation_id: str,
    attempt: int,
    delay_seconds: float,
    error_type: str,
    error_message: str,
) -> None:
    """One structured line per agent retry attempt."""
    log_event(
        "agent_retry",
        {
            "operation_id": operation_id,
            "attempt": attempt,
            "delay_seconds": round(delay_seconds, 3),
            "error_type": error_type,
            "error_message": error_message[:200],
        },
    )


def log_operation_failed(
    *,
    operation_id: str,
    session_id: str | None,
    model_id: str,
    gateway: str | None = None,
    error_type: str,
    error_message: str,
    wall_seconds: float,
    attempts: int,
) -> None:
    """Final failure after retries are exhausted."""
    log_event(
        "operation_failed",
        {
            "operation_id": operation_id,
            "session_id": session_id,
            "model_id": model_id,
            **({"gateway": gateway} if gateway else {}),
            "error_type": error_type,
            "error_message": error_message[:200],
            "wall_seconds": round(wall_seconds, 3),
            "attempts": attempts,
        },
    )


def log_tool_lifecycle(
    *,
    tool: str,
    phase: str,
    ok: bool | None = None,
    duration_ms: float | None = None,
    error: str | None = None,
) -> None:
    """Structured log for recon tool pre/post hooks."""
    payload: dict[str, object] = {"tool": tool, "phase": phase}
    if ok is not None:
        payload["ok"] = ok
    if duration_ms is not None:
        payload["duration_ms"] = round(duration_ms, 2)
    if error:
        payload["error"] = error[:300]
    log_event("tool_lifecycle", payload)


def log_session_billing_updated(
    *,
    session_id: str,
    total_billable_usd: float,
    total_cost_usd: float,
    operation_count: int,
) -> None:
    log_event(
        "session_billing_updated",
        {
            "session_id": session_id,
            "total_billable_usd": total_billable_usd,
            "total_cost_usd": total_cost_usd,
            "operation_count": operation_count,
        },
    )
