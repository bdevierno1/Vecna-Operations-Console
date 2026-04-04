"""Tool lifecycle payloads and hook wiring."""

from __future__ import annotations

import asyncio

from app.services.operation_context import attach_operation_runtime, detach_operation_runtime, make_tool_event_payload


def test_make_tool_event_payload_human_lines() -> None:
    start = make_tool_event_payload(tool="probe_common_paths", phase="start")
    assert start["type"] == "tool_lifecycle"
    assert "probe_common_paths" in start["human_line"]
    assert start["phase"] == "start"

    end_ok = make_tool_event_payload(
        tool="probe_common_paths", phase="end", duration_ms=12.3, ok=True
    )
    assert end_ok["ok"] is True
    assert "ms" in end_ok["human_line"]

    end_fail = make_tool_event_payload(
        tool="probe_common_paths", phase="end", duration_ms=5.0, ok=False, error="boom"
    )
    assert end_fail["ok"] is False
    assert "boom" in end_fail["human_line"]


def test_attach_detach_runtime() -> None:
    async def _go() -> None:
        loop = asyncio.get_running_loop()
        log: list[dict] = []
        tokens = attach_operation_runtime("00000000-0000-0000-0000-000000000001", log, loop, None)  # type: ignore[arg-type]
        detach_operation_runtime(tokens)

    asyncio.run(_go())
