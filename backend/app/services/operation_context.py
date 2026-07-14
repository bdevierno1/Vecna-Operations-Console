"""Per-operation runtime context for tool lifecycle hooks (before/after tool calls).

Strands runs tools via asyncio.to_thread with contextvars.copy_context(), so
operation_id_var and friends are visible inside tool threads.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
from contextvars import ContextVar, Token
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

logger = logging.getLogger(__name__)

operation_id_var: ContextVar[str | None] = ContextVar("vecna_operation_id", default=None)
events_log_var: ContextVar[list[dict[str, Any]] | None] = ContextVar("vecna_events_log", default=None)
main_loop_var: ContextVar[asyncio.AbstractEventLoop | None] = ContextVar("vecna_main_loop", default=None)
session_factory_var: ContextVar[async_sessionmaker[AsyncSession] | None] = ContextVar(
    "vecna_session_factory", default=None
)


def attach_operation_runtime(
    operation_id: str,
    events_log: list[dict[str, Any]],
    loop: asyncio.AbstractEventLoop,
    session_factory: async_sessionmaker[AsyncSession],
) -> tuple[Token, Token, Token, Token]:
    """Call at the start of execute_operation (same async task that awaits stream_async)."""
    return (
        operation_id_var.set(operation_id),
        events_log_var.set(events_log),
        main_loop_var.set(loop),
        session_factory_var.set(session_factory),
    )


def detach_operation_runtime(
    tokens: tuple[Token, Token, Token, Token],
) -> None:
    operation_id_var.reset(tokens[0])
    events_log_var.reset(tokens[1])
    main_loop_var.reset(tokens[2])
    session_factory_var.reset(tokens[3])


def make_tool_event_payload(
    *,
    tool: str,
    phase: str,
    duration_ms: float | None = None,
    ok: bool | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    if phase == "start":
        human = f"🔧 Tool «{tool}» starting…"
    elif phase == "end":
        if ok is True:
            suf = f" ({duration_ms:.0f} ms)" if duration_ms is not None else ""
            human = f"✓ Tool «{tool}» finished{suf}"
        elif ok is False:
            err = f" — {error}" if error else ""
            human = f"✗ Tool «{tool}» failed{err}"
        else:
            human = f"Tool «{tool}» end"
    else:
        human = f"Tool «{tool}» {phase}"
    return {
        "type": "tool_lifecycle",
        "tool": tool,
        "phase": phase,
        "human_line": human,
        **({"duration_ms": duration_ms} if duration_ms is not None else {}),
        **({"ok": ok} if ok is not None else {}),
        **({"error": str(error)[:500]} if error is not None else {}),
    }


async def _emit_tool_async(payload: dict[str, Any]) -> None:
    from app.hub import hub
    from app.models import Operation

    op_id = operation_id_var.get()
    if not op_id:
        return
    ev_log = events_log_var.get()
    if ev_log is not None:
        ev_log.append(payload)
    await hub.publish(op_id, {"type": "stream", "event": payload})
    sf = session_factory_var.get()
    if sf is not None and ev_log is not None:
        try:
            async with sf() as session:
                op = await session.get(Operation, op_id)
                if op:
                    op.events_json = list(ev_log)
                    await session.commit()
        except Exception as e:
            logger.warning("Persist tool lifecycle events failed: %s", e)


def schedule_tool_emit(payload: dict[str, Any]) -> None:
    """Schedule hub publish + DB persist from sync tool code (any thread with copied context)."""
    loop = main_loop_var.get()
    if loop is None or not loop.is_running():
        return
    fut = asyncio.run_coroutine_threadsafe(_emit_tool_async(payload), loop)

    # run_coroutine_threadsafe returns a concurrent.futures.Future, not an
    # asyncio.Future, so the done-callback must be typed against that.
    def _done(f: concurrent.futures.Future[None]) -> None:
        try:
            exc = f.exception()
            if exc:
                logger.warning("tool emit failed: %s", exc)
        except asyncio.CancelledError:
            pass

    fut.add_done_callback(_done)
