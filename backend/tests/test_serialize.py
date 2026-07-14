"""Stream-event serialization unit tests (app/lib/serialize.py).

Covers the human-readable line derivation used for the operation event stream
and structured report telemetry.
"""

from __future__ import annotations

from typing import Any

from app.lib.serialize import (
    _human_agent_result,
    _message_summary,
    _reasoning_snippet,
    _safe,
    _truncate,
    event_to_jsonable,
)


def _line(raw: dict[str, Any]) -> str:
    return event_to_jsonable(raw)["human_line"]


def test_lifecycle_marker_lines() -> None:
    assert _line({"init_event_loop": True}) == "Initializing agent…"
    assert _line({"start_event_loop": True}) == "LLM turn started"
    assert _line({"start": True}) == "Event loop starting"


def test_top_level_reasoning_text() -> None:
    line = _line({"reasoning": True, "reasoningText": "  think hard  "})
    assert line == "Reasoning: think hard"


def test_reasoning_via_delta_reasoning_content() -> None:
    snip = _reasoning_snippet({"delta": {"reasoningContent": {"text": "step 2"}}})
    assert snip == "step 2"
    assert _reasoning_snippet({"delta": {"reasoningContent": {}}}) is None
    assert _reasoning_snippet({"nothing": 1}) is None


def test_message_start_and_content_block_start_tooluse() -> None:
    assert _line({"event": {"messageStart": {"role": "assistant"}}}) == (
        "Assistant message (assistant)"
    )
    tool_line = _line(
        {"event": {"contentBlockStart": {"start": {"toolUse": {"name": "nmap"}}}}}
    )
    assert tool_line == "Tool call: nmap"
    assert _line({"event": {"contentBlockStart": {"start": {}}}}) == (
        "Content block started"
    )


def test_content_block_delta_variants() -> None:
    assert _line({"event": {"contentBlockDelta": {"delta": {"text": "hello"}}}}) == (
        "Text: hello"
    )
    assert _line(
        {"event": {"contentBlockDelta": {"delta": {"toolUse": {"input": "{a:1}"}}}}}
    ).startswith("Tool args:")
    assert _line(
        {
            "event": {
                "contentBlockDelta": {
                    "delta": {"reasoningContent": {"reasoningText": "why"}}
                }
            }
        }
    ) == "Reasoning: why"
    assert _line({"event": {"contentBlockDelta": {"delta": {}}}}) == (
        "Model output (delta)"
    )


def test_block_stop_message_stop_and_metadata() -> None:
    assert _line({"event": {"contentBlockStop": {}}}) == "Content block finished"
    assert _line({"event": {"messageStop": {"stopReason": "end_turn"}}}) == (
        "Message stopped (end_turn)"
    )
    assert _line(
        {"event": {"metadata": {"usage": {"inputTokens": 5, "outputTokens": 7}}}}
    ) == "Token usage: in 5 · out 7"
    assert _line({"event": {"metadata": {"other": 1}}}) == "Response metadata"


def test_fallback_line() -> None:
    assert _line({"unrecognized": "thing"}) == "Stream event"


def test_event_to_jsonable_drops_noise_keys() -> None:
    out = event_to_jsonable(
        {
            "agent": {"big": "obj"},
            "event_loop_cycle_trace": 1,
            "request_state": {},
            "keep": "yes",
        }
    )
    assert "agent" not in out
    assert "event_loop_cycle_trace" not in out
    assert "request_state" not in out
    assert out["keep"] == "yes"


def test_safe_handles_nested_and_unknown_types() -> None:
    class Weird:
        def __str__(self) -> str:
            return "weird-repr"

    result = _safe({"a": [1, "x", (2, 3)], "b": Weird(), "c": None, "d": True})
    assert result["a"] == [1, "x", [2, 3]]
    assert result["b"] == "weird-repr"
    assert result["c"] is None
    assert result["d"] is True


def test_truncate_shortens_long_strings() -> None:
    assert _truncate("abc", 10) == "abc"
    out = _truncate("x" * 50, 10)
    assert out.endswith("…")
    assert len(out) == 10


def test_message_summary_and_agent_result_helpers() -> None:
    summary = _message_summary({"content": [{"text": "a"}, {"notext": 1}, {"text": "b"}]})
    assert summary == "a\nb"
    # Non-mapping message falls back to str().
    assert _message_summary(12345) == "12345"

    human = _human_agent_result(
        {
            "message": "line one\nline two",
            "metrics": {"cycle_count": 3, "tool_calls": {"nmap": 2, "curl": 1}},
        }
    )
    assert "Cycle 3" in human
    assert "curl×1" in human
    assert "nmap×2" in human
