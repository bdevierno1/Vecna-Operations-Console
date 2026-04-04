"""Best-effort JSON serialization for Strands stream events."""

from __future__ import annotations

from typing import Any

from strands.agent.agent_result import AgentResult

# human_line must carry full cumulative streaming text so the UI can merge deltas.
# Truncating each chunk to ~100 chars breaks prefix-based merge and produces smashed/duplicated text.
STREAM_HUMAN_LINE_MAX = 120_000

# Omit from persisted/streamed JSON — not useful in UI and bloats raw view.
_DROP_FROM_STREAM_EVENT = frozenset(
    {
        "agent",
        "event_loop_cycle_trace",
        "event_loop_cycle_span",
    }
)


def event_to_jsonable(raw: dict[str, Any]) -> dict[str, Any]:
    if "result" in raw and isinstance(raw["result"], AgentResult):
        r = raw["result"]
        payload = {
            "type": "agent_result",
            "stop_reason": str(r.stop_reason),
            "message": _message_summary(r.message),
            "metrics": {
                "cycle_count": r.metrics.cycle_count,
                "tool_calls": {name: tm.call_count for name, tm in r.metrics.tool_metrics.items()},
            },
        }
        payload["human_line"] = _human_agent_result(payload)
        return payload
    out: dict[str, Any] = {}
    for k, v in raw.items():
        if k == "result":
            continue
        if k in _DROP_FROM_STREAM_EVENT:
            continue
        if k == "request_state" and v == {}:
            continue
        out[k] = _safe(v)
    out["human_line"] = _human_line_for_stream_event(out)
    return out


def _truncate(s: str, max_len: int) -> str:
    s = s.strip()
    if len(s) <= max_len:
        return s
    return s[: max_len - 1] + "…"


def _message_summary(message: Any) -> str:
    try:
        parts: list[str] = []
        for block in message.get("content", []) or []:
            if isinstance(block, dict) and "text" in block:
                parts.append(block["text"])
        return "\n".join(parts)[:120_000]
    except Exception:
        return str(message)[:10_000]


def _safe(v: Any) -> Any:
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    if isinstance(v, dict):
        return {str(k): _safe(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_safe(x) for x in v]
    return str(v)[:8000]


def _human_agent_result(payload: dict[str, Any]) -> str:
    m = payload.get("metrics") or {}
    tools = m.get("tool_calls") or {}
    parts = [f"Cycle {m.get('cycle_count', '?')}"]
    if tools:
        tc = ", ".join(f"{n}×{c}" for n, c in sorted(tools.items()))
        parts.append(f"tools {tc}")
    msg = (payload.get("message") or "").strip()
    if msg:
        parts.append(_truncate(msg.replace("\n", " "), 120))
    return " · ".join(parts)


def _reasoning_snippet(ev: dict[str, Any]) -> str | None:
    """Strands emits reasoning as top-level reasoningText or nested delta.reasoningContent.text."""
    rt = ev.get("reasoningText")
    if isinstance(rt, str) and rt.strip():
        return _truncate(rt.strip(), STREAM_HUMAN_LINE_MAX)
    delta = ev.get("delta")
    if isinstance(delta, dict):
        rc = delta.get("reasoningContent")
        if isinstance(rc, dict):
            t = rc.get("text")
            if isinstance(t, str) and t.strip():
                return _truncate(t.strip(), STREAM_HUMAN_LINE_MAX)
    return None


def _human_line_for_stream_event(ev: dict[str, Any]) -> str:
    if ev.get("init_event_loop"):
        return "Initializing agent…"
    if ev.get("start_event_loop"):
        return "LLM turn started"
    if ev.get("start"):
        return "Event loop starting"
    # Top-level reasoning deltas (Strands event_loop_cycle, not only LiteLLM chunk)
    if ev.get("reasoning") is True or "reasoningText" in ev:
        snip = _reasoning_snippet(ev)
        if snip:
            return f"Reasoning: {snip}"
    chunk = ev.get("event")
    if isinstance(chunk, dict):
        if "messageStart" in chunk:
            role = (chunk.get("messageStart") or {}).get("role") or "assistant"
            return f"Assistant message ({role})"
        if "contentBlockStart" in chunk:
            start = (chunk.get("contentBlockStart") or {}).get("start") or {}
            tu = start.get("toolUse") if isinstance(start, dict) else None
            if isinstance(tu, dict) and tu.get("name"):
                return f"Tool call: {tu['name']}"
            return "Content block started"
        if "contentBlockDelta" in chunk:
            delta = (chunk.get("contentBlockDelta") or {}).get("delta") or {}
            if isinstance(delta, dict):
                if delta.get("text"):
                    return f"Text: {_truncate(str(delta['text']), STREAM_HUMAN_LINE_MAX)}"
                tu = delta.get("toolUse")
                if isinstance(tu, dict) and tu.get("input"):
                    return f"Tool args: {_truncate(str(tu['input']), STREAM_HUMAN_LINE_MAX)}"
                rc = delta.get("reasoningContent")
                if isinstance(rc, dict):
                    # OpenAI-style { "reasoningText": "..." } or { "text": "..." }
                    rtxt = rc.get("reasoningText") or rc.get("text")
                    if rtxt:
                        return f"Reasoning: {_truncate(str(rtxt), STREAM_HUMAN_LINE_MAX)}"
            return "Model output (delta)"
        if "contentBlockStop" in chunk:
            return "Content block finished"
        if "messageStop" in chunk:
            ms = chunk.get("messageStop") or {}
            sr = ms.get("stopReason") if isinstance(ms, dict) else None
            return f"Message stopped ({sr or 'done'})"
        if "metadata" in chunk:
            meta = chunk.get("metadata") or {}
            usage = meta.get("usage") if isinstance(meta, dict) else None
            if isinstance(usage, dict):
                inp = usage.get("inputTokens") or usage.get("prompt_tokens")
                out_t = usage.get("outputTokens") or usage.get("completion_tokens")
                if inp is not None or out_t is not None:
                    return f"Token usage: in {inp} · out {out_t}"
            return "Response metadata"
    # Fallback: top-level reasoning only (no `event` wrapper)
    snip = _reasoning_snippet(ev)
    if snip:
        return f"Reasoning: {snip}"
    return "Stream event"
