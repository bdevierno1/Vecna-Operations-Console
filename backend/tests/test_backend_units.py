"""Unit coverage for structured logging, report building, and tool-lifecycle context.

These modules are exercised indirectly by the DORA feature's request/telemetry
path; this suite pins their behaviour directly so the backend coverage gate holds.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

from app.lib.report import build_structured_report
from app.services import operation_context as octx
from app.services import telemetry

UTC = timezone.utc


# ---------------------------------------------------------------------------
# telemetry
# ---------------------------------------------------------------------------
def _capture(caplog: Any) -> list[str]:
    return [r.message for r in caplog.records]


def test_log_event_emits_single_json_line(caplog: Any) -> None:
    with caplog.at_level(logging.INFO, logger="vecna.telemetry"):
        telemetry.log_event("demo", {"a": 1, "obj": object()})
    msgs = _capture(caplog)
    assert any("demo" in m for m in msgs)


def test_log_llm_query_with_and_without_gateway(caplog: Any) -> None:
    with caplog.at_level(logging.INFO, logger="vecna.telemetry"):
        telemetry.log_llm_query(operation_id="op1", model_id="m", gateway="gw")
        telemetry.log_llm_query(operation_id="op2", model_id="m")
    msgs = " ".join(_capture(caplog))
    assert "gw" in msgs
    assert "op2" in msgs


def test_log_operation_completed_and_failed(caplog: Any) -> None:
    with caplog.at_level(logging.INFO, logger="vecna.telemetry"):
        telemetry.log_operation_completed(
            operation_id="op",
            session_id="s",
            model_id="m",
            gateway="gw",
            cost_usd=1.0,
            billable_total_usd=2.0,
            tool_units=3,
            input_tokens=10,
            output_tokens=20,
            wall_seconds=1.2345,
            cost_estimate_unknown=True,
        )
        telemetry.log_operation_failed(
            operation_id="op",
            session_id=None,
            model_id="m",
            error_type="Boom",
            error_message="x" * 500,
            wall_seconds=0.5,
            attempts=3,
        )
    msgs = " ".join(_capture(caplog))
    assert "operation_completed" in msgs
    assert "operation_failed" in msgs


def test_log_retry_unknown_cost_lifecycle_and_billing(caplog: Any) -> None:
    with caplog.at_level(logging.INFO, logger="vecna.telemetry"):
        telemetry.log_agent_retry(
            operation_id="op",
            attempt=2,
            delay_seconds=0.5,
            error_type="T",
            error_message="m" * 400,
        )
        telemetry.log_unknown_model_cost(model_id="mystery")
        telemetry.log_tool_lifecycle(
            tool="nmap", phase="end", ok=True, duration_ms=12.5, error=None
        )
        telemetry.log_tool_lifecycle(
            tool="nmap", phase="end", ok=False, error="e" * 400
        )
        telemetry.log_session_billing_updated(
            session_id="s",
            total_billable_usd=3.0,
            total_cost_usd=2.0,
            operation_count=4,
        )
    msgs = " ".join(_capture(caplog))
    assert "agent_retry" in msgs
    assert "unknown_model_cost" in msgs
    assert "tool_lifecycle" in msgs
    assert "session_billing_updated" in msgs


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------
def _fake_operation(**overrides: Any) -> SimpleNamespace:
    base: dict[str, Any] = dict(
        id="op-1",
        target_url="https://example.com",
        status="completed",
        session_id="sess-1",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        credits_used=5,
        error_message=None,
        summary_text="all good",
        findings_json=[
            {"category": "dns", "title": "SPF"},
            {"category": "Headers", "title": "CSP"},
            {"category": "weird", "title": "unknown-cat"},
            "not-a-dict",
        ],
        events_json=[{"type": "x"}, {"type": "y"}],
        cost_usd=1.0,
        scan_fee_usd=0.5,
        tool_fee_usd=0.25,
        billable_total_usd=0.0,
        duration_seconds=12.0,
        tool_units=7,
        pricing_breakdown_json={"k": "v"},
        cost_estimate_unknown=False,
        input_tokens=100,
        output_tokens=200,
        cache_read_tokens=10,
        cache_write_tokens=20,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_build_structured_report_full_shape() -> None:
    report = build_structured_report(_fake_operation())
    assert report["schema_version"] == "1.0"
    assert report["operation"]["id"] == "op-1"
    assert report["operation"]["created_at"] == "2026-01-01T00:00:00+00:00"
    # billable_total falls back to llm + scan + tool when stored is 0.
    assert report["telemetry"]["billable_total_usd"] == 1.75
    assert report["telemetry"]["stream_event_count"] == 2
    grouped = report["findings_by_category"]
    assert "dns" in grouped
    assert "headers" in grouped
    # Unknown category and non-dict finding fold into "other".
    assert "other" in grouped
    assert len(grouped["other"]) == 2


def test_build_structured_report_prefers_stored_billable_and_handles_nulls() -> None:
    report = build_structured_report(
        _fake_operation(
            billable_total_usd=9.0,
            created_at=None,
            findings_json=None,
            events_json=None,
            summary_text=None,
            tool_units=None,
        )
    )
    assert report["telemetry"]["billable_total_usd"] == 9.0
    assert report["operation"]["created_at"] is None
    assert report["findings"] == []
    assert report["telemetry"]["stream_event_count"] == 0
    assert report["summary_markdown"] == ""
    assert report["telemetry"]["tool_units"] == 0


# ---------------------------------------------------------------------------
# operation_context
# ---------------------------------------------------------------------------
def test_make_tool_event_payload_phases() -> None:
    start = octx.make_tool_event_payload(tool="nmap", phase="start")
    assert start["phase"] == "start"
    assert "starting" in start["human_line"]

    ok = octx.make_tool_event_payload(
        tool="nmap", phase="end", ok=True, duration_ms=42.0
    )
    assert "finished" in ok["human_line"]
    assert ok["duration_ms"] == 42.0

    ok_no_dur = octx.make_tool_event_payload(tool="nmap", phase="end", ok=True)
    assert "finished" in ok_no_dur["human_line"]

    failed = octx.make_tool_event_payload(
        tool="nmap", phase="end", ok=False, error="boom"
    )
    assert "failed" in failed["human_line"]
    assert failed["error"] == "boom"

    ambiguous = octx.make_tool_event_payload(tool="nmap", phase="end")
    assert ambiguous["human_line"].endswith("end")

    other = octx.make_tool_event_payload(tool="nmap", phase="progress")
    assert "progress" in other["human_line"]


def test_attach_and_detach_runtime_roundtrip() -> None:
    loop = asyncio.new_event_loop()
    try:
        events: list[dict[str, Any]] = []
        tokens = octx.attach_operation_runtime("op-9", events, loop, None)  # type: ignore[arg-type]
        assert octx.operation_id_var.get() == "op-9"
        assert octx.events_log_var.get() is events
        assert octx.main_loop_var.get() is loop
        octx.detach_operation_runtime(tokens)
        assert octx.operation_id_var.get() is None
        assert octx.main_loop_var.get() is None
    finally:
        loop.close()


def test_schedule_tool_emit_no_loop_is_noop() -> None:
    # No main loop set in context -> returns without scheduling.
    token = octx.main_loop_var.set(None)
    try:
        octx.schedule_tool_emit({"type": "tool_lifecycle"})
    finally:
        octx.main_loop_var.reset(token)


def test_emit_tool_async_without_operation_id_returns_early() -> None:
    async def run() -> None:
        token = octx.operation_id_var.set(None)
        try:
            await octx._emit_tool_async({"type": "tool_lifecycle"})
        finally:
            octx.operation_id_var.reset(token)

    asyncio.run(run())


def test_emit_tool_async_appends_and_publishes_when_active() -> None:
    async def run() -> None:
        events: list[dict[str, Any]] = []
        t1 = octx.operation_id_var.set("op-active")
        t2 = octx.events_log_var.set(events)
        t3 = octx.session_factory_var.set(None)
        try:
            await octx._emit_tool_async({"type": "tool_lifecycle", "x": 1})
        finally:
            octx.operation_id_var.reset(t1)
            octx.events_log_var.reset(t2)
            octx.session_factory_var.reset(t3)
        # payload appended to the in-memory event log; publish did not raise.
        assert events and events[0]["x"] == 1

    asyncio.run(run())


def test_schedule_tool_emit_with_running_loop_dispatches() -> None:
    async def run() -> None:
        loop = asyncio.get_running_loop()
        events: list[dict[str, Any]] = []
        t0 = octx.main_loop_var.set(loop)
        t1 = octx.operation_id_var.set("op-sched")
        t2 = octx.events_log_var.set(events)
        t3 = octx.session_factory_var.set(None)
        try:
            octx.schedule_tool_emit({"type": "tool_lifecycle", "z": 9})
            # Let the cross-thread-scheduled coroutine and done-callback run.
            await asyncio.sleep(0.1)
        finally:
            octx.main_loop_var.reset(t0)
            octx.operation_id_var.reset(t1)
            octx.events_log_var.reset(t2)
            octx.session_factory_var.reset(t3)
        assert any(e.get("z") == 9 for e in events)

    asyncio.run(run())
