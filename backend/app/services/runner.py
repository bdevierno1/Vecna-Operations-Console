from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent.tools import set_findings_bucket
from agent.vecna_agent import build_agent, user_message_for_target
from app.hub import hub
from app.lib.serialize import event_to_jsonable
from app.models import Operation
from app.services.billing import record_operation_for_session
from app.services.cost import UsageSummary, usage_from_metrics
from app.services.errors import classify_agent_error, is_retryable, retry_delay_seconds, user_message_for_error
from app.services.http_client import detect_gateway_from_model_id
from app.services.operation_context import attach_operation_runtime, detach_operation_runtime
from app.services.pricing import billable_for_completed_scan, breakdown_to_dict
from app.services.telemetry import log_agent_retry, log_llm_query, log_operation_completed, log_operation_failed, log_unknown_model_cost
from strands.agent.agent_result import AgentResult

logger = logging.getLogger(__name__)

_MAX_AGENT_RETRIES = 6  # 6 retries = 7 total attempts

# Registry of running asyncio tasks keyed by operation_id — used by the
# cancel endpoint to interrupt in-flight agent runs.
_running_tasks: dict[str, asyncio.Task] = {}


def register_task(operation_id: str, task: asyncio.Task) -> None:
    _running_tasks[operation_id] = task


def cancel_task(operation_id: str) -> bool:
    """Cancel a running operation. Returns True if a task was found and cancelled."""
    task = _running_tasks.pop(operation_id, None)
    if task and not task.done():
        task.cancel()
        return True
    return False


def _deregister_task(operation_id: str) -> None:
    _running_tasks.pop(operation_id, None)


async def _persist(
    session_factory: async_sessionmaker[AsyncSession],
    operation_id: str,
    *,
    status: str | None = None,
    events_json: list | None = None,
    findings_json: list | None = None,
    credits_used: float | None = None,
    cost_usd: float | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    cache_read_tokens: int | None = None,
    cache_write_tokens: int | None = None,
    duration_seconds: float | None = None,
    summary_text: str | None = None,
    error_message: str | None = None,
    cost_estimate_unknown: bool | None = None,
    scan_fee_usd: float | None = None,
    tool_fee_usd: float | None = None,
    tool_units: int | None = None,
    billable_total_usd: float | None = None,
    pricing_breakdown_json: dict | None = None,
) -> None:
    async with session_factory() as session:
        op = await session.get(Operation, operation_id)
        if not op:
            return
        if status is not None:
            op.status = status
        if events_json is not None:
            op.events_json = events_json
        if findings_json is not None:
            op.findings_json = findings_json
        if credits_used is not None:
            op.credits_used = credits_used
        if cost_usd is not None:
            op.cost_usd = cost_usd
        if input_tokens is not None:
            op.input_tokens = input_tokens
        if output_tokens is not None:
            op.output_tokens = output_tokens
        if cache_read_tokens is not None:
            op.cache_read_tokens = cache_read_tokens
        if cache_write_tokens is not None:
            op.cache_write_tokens = cache_write_tokens
        if duration_seconds is not None:
            op.duration_seconds = duration_seconds
        if summary_text is not None:
            op.summary_text = summary_text
        if error_message is not None:
            op.error_message = error_message
        if cost_estimate_unknown is not None:
            op.cost_estimate_unknown = cost_estimate_unknown
        if scan_fee_usd is not None:
            op.scan_fee_usd = scan_fee_usd
        if tool_fee_usd is not None:
            op.tool_fee_usd = tool_fee_usd
        if tool_units is not None:
            op.tool_units = tool_units
        if billable_total_usd is not None:
            op.billable_total_usd = billable_total_usd
        if pricing_breakdown_json is not None:
            op.pricing_breakdown_json = pricing_breakdown_json
        session.add(op)
        await session.commit()


async def execute_operation(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Any,
    operation_id: str,
    target_url: str,
    *,
    billing_session_id: str | None = None,
) -> None:
    findings: list[dict] = []
    set_findings_bucket(findings)
    events_log: list[dict] = []
    running_credit_estimate = 0.0
    final_usage = UsageSummary()
    summary = ""
    last_finding_count = 0
    wall_start = time.monotonic()
    attempt = 0

    await _persist(session_factory, operation_id, status="running", events_json=[], findings_json=[])

    gateway = detect_gateway_from_model_id(settings.litellm_model_id)
    log_llm_query(
        operation_id=operation_id,
        model_id=settings.litellm_model_id,
        gateway=gateway,
    )

    agent = build_agent(settings)
    prompt = user_message_for_target(target_url)

    rt_tokens = attach_operation_runtime(
        operation_id,
        events_log,
        asyncio.get_running_loop(),
        session_factory,
    )
    try:
        while True:
            attempt += 1
            try:
                async for raw in agent.stream_async(prompt):
                    if not isinstance(raw, dict):
                        continue

                    if raw.get("start_event_loop"):
                        running_credit_estimate += 0.5
                        await hub.publish(
                            operation_id,
                            {"type": "credit", "partial": True},
                        )

                    if "result" in raw and isinstance(raw["result"], AgentResult):
                        r = raw["result"]
                        final_usage = usage_from_metrics(settings.litellm_model_id, r.metrics)
                        summary = _text_from_message(r.message)
                        safe = event_to_jsonable(raw)
                        events_log.append(safe)
                        await hub.publish(operation_id, {"type": "stream", "event": safe})
                        await hub.publish(
                            operation_id,
                            {
                                "type": "credit",
                                "cost_usd": final_usage.cost_usd,
                                "input_tokens": final_usage.input_tokens,
                                "output_tokens": final_usage.output_tokens,
                                "partial": False,
                            },
                        )
                        if len(findings) > last_finding_count:
                            last_finding_count = len(findings)
                            await hub.publish(
                                operation_id,
                                {"type": "findings", "items": list(findings)},
                            )
                        await _persist(session_factory, operation_id, events_json=list(events_log), findings_json=list(findings))
                        continue

                    safe = event_to_jsonable(raw)
                    events_log.append(safe)
                    await hub.publish(operation_id, {"type": "stream", "event": safe})

                    if len(findings) > last_finding_count:
                        last_finding_count = len(findings)
                        await hub.publish(
                            operation_id,
                            {"type": "findings", "items": list(findings)},
                        )

                    await _persist(session_factory, operation_id, events_json=list(events_log), findings_json=list(findings))

                # --- Stream completed successfully ---
                break

            except asyncio.CancelledError:
                cancel_duration = max(0.0, time.monotonic() - wall_start)
                await _persist(
                    session_factory,
                    operation_id,
                    status="cancelled",
                    error_message="Operation cancelled by user.",
                    duration_seconds=cancel_duration,
                    events_json=list(events_log),
                    findings_json=list(findings),
                )
                await hub.publish(operation_id, {"type": "cancelled", "message": "Operation cancelled."})
                set_findings_bucket(None)
                _deregister_task(operation_id)
                raise

            except Exception as exc:
                error_type = classify_agent_error(exc)
                retryable = is_retryable(exc)
                retries_left = _MAX_AGENT_RETRIES - (attempt - 1)

                logger.warning(
                    "Agent error (attempt %d, type=%s, retryable=%s): %s",
                    attempt, error_type, retryable, exc,
                )

                if retryable and retries_left > 0:
                    delay = retry_delay_seconds(attempt, exc)
                    log_agent_retry(
                        operation_id=operation_id,
                        attempt=attempt,
                        delay_seconds=delay,
                        error_type=error_type,
                        error_message=str(exc),
                    )
                    await hub.publish(
                        operation_id,
                        {
                            "type": "retry",
                            "attempt": attempt,
                            "delay_seconds": round(delay, 1),
                            "error_type": error_type,
                            "message": f"Retrying in {delay:.0f}s (attempt {attempt}/{_MAX_AGENT_RETRIES + 1})…",
                        },
                    )
                    await asyncio.sleep(delay)
                    # Rebuild the agent so any stale connection is dropped
                    agent = build_agent(settings)
                    continue

                # Non-retryable or exhausted retries — fail the operation
                user_msg = user_message_for_error(exc)
                fail_duration = max(0.0, time.monotonic() - wall_start)
                log_operation_failed(
                    operation_id=operation_id,
                    session_id=billing_session_id,
                    model_id=settings.litellm_model_id,
                    gateway=gateway,
                    error_type=error_type,
                    error_message=str(exc),
                    wall_seconds=fail_duration,
                    attempts=attempt,
                )
                await _persist(
                    session_factory,
                    operation_id,
                    status="failed",
                    error_message=user_msg,
                    duration_seconds=fail_duration,
                    events_json=list(events_log),
                    findings_json=list(findings),
                )
                await hub.publish(
                    operation_id,
                    {
                        "type": "error",
                        "message": user_msg,
                        "error_type": error_type,
                        "attempts": attempt,
                    },
                )
                set_findings_bucket(None)
                _deregister_task(operation_id)
                return

        # --- Successful completion ---
        set_findings_bucket(None)

        if final_usage.credits == 0.0 and running_credit_estimate > 0.0:
            final_usage.credits = running_credit_estimate

        wall_seconds = max(0.0, time.monotonic() - wall_start)
        billable = billable_for_completed_scan(final_usage, settings)
        session_billing: dict | None = None
        if billing_session_id:
            snap = await record_operation_for_session(
                session_factory,
                billing_session_id=billing_session_id,
                model_id=settings.litellm_model_id,
                usage=final_usage,
                wall_seconds=wall_seconds,
                billable=billable,
            )
            session_billing = snap.to_api_dict()

        if final_usage.has_unknown_model_cost:
            log_unknown_model_cost(model_id=settings.litellm_model_id)

        log_operation_completed(
            operation_id=operation_id,
            session_id=billing_session_id,
            model_id=settings.litellm_model_id,
            gateway=gateway,
            cost_usd=final_usage.cost_usd,
            billable_total_usd=billable.billable_total_usd,
            tool_units=billable.tool_units,
            input_tokens=final_usage.input_tokens,
            output_tokens=final_usage.output_tokens,
            wall_seconds=wall_seconds,
            cost_estimate_unknown=final_usage.has_unknown_model_cost,
        )

        await _persist(
            session_factory,
            operation_id,
            status="completed",
            credits_used=final_usage.credits,
            cost_usd=final_usage.cost_usd,
            input_tokens=final_usage.input_tokens,
            output_tokens=final_usage.output_tokens,
            cache_read_tokens=final_usage.cache_read_tokens,
            cache_write_tokens=final_usage.cache_write_tokens,
            duration_seconds=wall_seconds,
            cost_estimate_unknown=final_usage.has_unknown_model_cost,
            summary_text=summary,
            findings_json=list(findings),
            events_json=list(events_log),
            scan_fee_usd=billable.scan_fee_usd,
            tool_fee_usd=billable.tool_fee_usd,
            tool_units=billable.tool_units,
            billable_total_usd=billable.billable_total_usd,
            pricing_breakdown_json=breakdown_to_dict(billable),
        )
        done_msg: dict = {
            "type": "done",
            "cost_usd": final_usage.cost_usd,
            "billable_total_usd": billable.billable_total_usd,
            "billable_breakdown": breakdown_to_dict(billable),
            "input_tokens": final_usage.input_tokens,
            "output_tokens": final_usage.output_tokens,
            "duration_seconds": wall_seconds,
            "findings": list(findings),
            "summary": summary[:20_000],
            "attempts": attempt,
        }
        if session_billing is not None:
            done_msg["session_billing"] = session_billing
        done_msg["cost_estimate_unknown"] = final_usage.has_unknown_model_cost
        await hub.publish(operation_id, done_msg)
    finally:
        detach_operation_runtime(rt_tokens)


def _text_from_message(message: Any) -> str:
    parts: list[str] = []
    try:
        for block in message.get("content", []) or []:
            if isinstance(block, dict) and "text" in block:
                parts.append(block["text"])
    except Exception:
        return str(message)
    return "\n".join(parts)
