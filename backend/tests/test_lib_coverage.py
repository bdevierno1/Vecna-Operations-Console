"""Unit coverage for pure helpers: url_guard, serialize, report."""

from __future__ import annotations

from types import SimpleNamespace

from strands.agent.agent_result import AgentResult

from app.lib.report import build_structured_report
from app.lib.serialize import event_to_jsonable
from app.models import Operation
from app.url_guard import is_safe_public_target


def test_url_guard_allows_public_host() -> None:
    ok, reason = is_safe_public_target("https://example.com/path")
    assert ok
    assert reason == ""
    # scheme is added when missing
    ok2, _ = is_safe_public_target("example.com")
    assert ok2


def test_url_guard_blocks_loopback_and_metadata() -> None:
    for target in (
        "http://localhost:8000",
        "https://metadata.google.internal",
        "https://app.localhost",
        "https://0.0.0.0",
    ):
        ok, reason = is_safe_public_target(target)
        assert not ok, target
        assert reason


def test_url_guard_blocks_private_and_special_ips() -> None:
    for target in (
        "http://127.0.0.1",
        "http://10.0.0.5",
        "http://192.168.1.1",
        "http://169.254.169.254",
        "http://172.16.0.1",
    ):
        ok, _ = is_safe_public_target(target)
        assert not ok, target


def test_url_guard_missing_host_and_ipv6_bracket() -> None:
    ok, reason = is_safe_public_target("http://")
    assert not ok
    assert reason == "Missing host"
    ok2, reason2 = is_safe_public_target("http://[::1]/")
    assert not ok2
    assert reason2


def test_url_guard_invalid_ipv4_literal() -> None:
    # matches the dotted-quad regex but is not a valid address → "Invalid IP"
    ok, reason = is_safe_public_target("http://999.1.1.1")
    assert not ok
    assert reason == "Invalid IP"


def test_serialize_lifecycle_events() -> None:
    assert event_to_jsonable({"init_event_loop": True})["human_line"] == "Initializing agent…"
    assert event_to_jsonable({"start_event_loop": True})["human_line"] == "LLM turn started"
    assert event_to_jsonable({"start": True})["human_line"] == "Event loop starting"


def test_serialize_reasoning_and_drops() -> None:
    out = event_to_jsonable(
        {"reasoning": True, "reasoningText": "thinking hard", "agent": "drop-me"}
    )
    assert out["human_line"].startswith("Reasoning:")
    assert "agent" not in out


def test_serialize_content_block_events() -> None:
    tool_start = {"event": {"contentBlockStart": {"start": {"toolUse": {"name": "scan"}}}}}
    assert event_to_jsonable(tool_start)["human_line"] == "Tool call: scan"

    text_delta = {"event": {"contentBlockDelta": {"delta": {"text": "hello"}}}}
    assert event_to_jsonable(text_delta)["human_line"] == "Text: hello"

    msg_start = {"event": {"messageStart": {"role": "assistant"}}}
    assert event_to_jsonable(msg_start)["human_line"] == "Assistant message (assistant)"

    stop = {"event": {"messageStop": {"stopReason": "end_turn"}}}
    assert event_to_jsonable(stop)["human_line"] == "Message stopped (end_turn)"


def test_serialize_metadata_usage_and_safe() -> None:
    meta = {"event": {"metadata": {"usage": {"inputTokens": 10, "outputTokens": 5}}}}
    assert "Token usage" in event_to_jsonable(meta)["human_line"]
    # _safe coerces nested/non-primitive values
    out = event_to_jsonable({"nested": {"k": (1, 2)}, "obj": object()})
    assert out["nested"] == {"k": [1, 2]}
    assert isinstance(out["obj"], str)


def test_serialize_fallback_stream_event() -> None:
    assert event_to_jsonable({"unknown": "x"})["human_line"] == "Stream event"


def test_serialize_agent_result_branch() -> None:
    result = AgentResult(
        stop_reason="end_turn",
        message={"content": [{"text": "final answer"}, {"noise": 1}]},
        metrics=SimpleNamespace(
            cycle_count=3,
            tool_metrics={"scan": SimpleNamespace(call_count=2)},
        ),
        state={},
    )
    out = event_to_jsonable({"result": result})
    assert out["type"] == "agent_result"
    assert out["metrics"]["cycle_count"] == 3
    assert out["metrics"]["tool_calls"] == {"scan": 2}
    assert "Cycle 3" in out["human_line"]
    assert "scan×2" in out["human_line"]
    assert "final answer" in out["human_line"]


def test_serialize_more_content_blocks() -> None:
    # nested delta reasoningContent → fallback reasoning snippet
    assert event_to_jsonable(
        {"delta": {"reasoningContent": {"text": "deep thought"}}}
    )["human_line"] == "Reasoning: deep thought"
    # tool-args delta
    tool_args = {"event": {"contentBlockDelta": {"delta": {"toolUse": {"input": "{}"}}}}}
    assert event_to_jsonable(tool_args)["human_line"].startswith("Tool args:")
    # content block start without toolUse, and content block stop
    assert (
        event_to_jsonable({"event": {"contentBlockStart": {"start": {}}}})["human_line"]
        == "Content block started"
    )
    assert (
        event_to_jsonable({"event": {"contentBlockStop": {}}})["human_line"]
        == "Content block finished"
    )
    # metadata without usage numbers → generic label
    assert (
        event_to_jsonable({"event": {"metadata": {}}})["human_line"] == "Response metadata"
    )
    # OpenAI-style reasoningContent nested in a contentBlockDelta
    reasoning_delta = {
        "event": {"contentBlockDelta": {"delta": {"reasoningContent": {"reasoningText": "why"}}}}
    }
    assert event_to_jsonable(reasoning_delta)["human_line"] == "Reasoning: why"


def test_build_structured_report_full() -> None:
    op = Operation(
        id="op-1",
        target_url="https://example.com",
        status="completed",
        session_id="sess-1",
        summary_text="done",
        findings_json=[
            {"category": "dns", "title": "a"},
            {"category": "weird", "title": "b"},
            "raw-string-finding",
        ],
        events_json=[{"x": 1}],
        cost_usd=1.0,
        scan_fee_usd=0.5,
        tool_fee_usd=0.25,
    )
    report = build_structured_report(op)
    assert report["schema_version"] == "1.0"
    assert report["operation"]["id"] == "op-1"
    assert report["telemetry"]["stream_event_count"] == 1
    grouped = report["findings_by_category"]
    assert "dns" in grouped
    # unknown category and raw string both fold into "other"
    assert len(grouped["other"]) == 2


def test_build_structured_report_empty_defaults() -> None:
    op = Operation(id="op-2", target_url="https://ex.com", status="pending")
    report = build_structured_report(op)
    assert report["summary_markdown"] == ""
    assert report["findings"] == []
    assert report["findings_by_category"] == {}
