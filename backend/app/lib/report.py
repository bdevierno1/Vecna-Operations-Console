"""Structured JSON report for completed operations (take-home / API export)."""

from __future__ import annotations

from typing import Any

from app.models import Operation
from app.services.pricing import display_billable_total

_SCHEMA_VERSION = "1.0"


def _group_findings(findings: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    sections: dict[str, list[dict[str, Any]]] = {"dns": [], "headers": [], "paths": [], "other": []}
    for f in findings:
        if not isinstance(f, dict):
            sections["other"].append({"title": str(f)})
            continue
        cat = (f.get("category") or "other").lower()
        if cat not in sections:
            cat = "other"
        sections[cat].append(f)
    return {k: v for k, v in sections.items() if v}


def build_structured_report(op: Operation) -> dict[str, Any]:
    """Fixed schema for GET /api/operations/{id}/report.json and downloads."""
    findings = op.findings_json or []
    events = op.events_json or []
    llm = op.cost_usd or 0.0
    scan = op.scan_fee_usd or 0.0
    tool_f = op.tool_fee_usd or 0.0
    billable = display_billable_total(
        llm_cost_usd=llm,
        scan_fee_usd=scan,
        tool_fee_usd=tool_f,
        stored_billable_usd=op.billable_total_usd or 0.0,
    )
    return {
        "schema_version": _SCHEMA_VERSION,
        "operation": {
            "id": op.id,
            "target_url": op.target_url,
            "status": op.status,
            "session_id": op.session_id,
            "created_at": op.created_at.isoformat() if op.created_at else None,
            "credits_used": op.credits_used,
            "error_message": op.error_message,
        },
        "summary_markdown": op.summary_text or "",
        "findings": findings,
        "findings_by_category": _group_findings(findings),
        "telemetry": {
            "stream_event_count": len(events),
            "duration_seconds": op.duration_seconds,
            "cost_usd": llm,
            "scan_fee_usd": scan,
            "tool_fee_usd": tool_f,
            "tool_units": op.tool_units or 0,
            "billable_total_usd": billable,
            "pricing_breakdown": op.pricing_breakdown_json,
            "cost_estimate_unknown": bool(op.cost_estimate_unknown),
            "input_tokens": op.input_tokens or 0,
            "output_tokens": op.output_tokens or 0,
            "cache_read_tokens": op.cache_read_tokens or 0,
            "cache_write_tokens": op.cache_write_tokens or 0,
        },
    }
