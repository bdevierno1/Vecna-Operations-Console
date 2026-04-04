"""SQLite additive migrations (create_all does not add new columns to existing tables)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import inspect, text


def _sqlite_add_missing_columns(sync_conn: Any) -> None:
    insp = inspect(sync_conn)
    if insp.has_table("operations"):
        cols = {c["name"] for c in insp.get_columns("operations")}
        alters: list[str] = []
        if "session_id" not in cols:
            alters.append("ALTER TABLE operations ADD COLUMN session_id VARCHAR(36)")
        if "duration_seconds" not in cols:
            alters.append("ALTER TABLE operations ADD COLUMN duration_seconds REAL")
        if "cost_estimate_unknown" not in cols:
            alters.append("ALTER TABLE operations ADD COLUMN cost_estimate_unknown INTEGER DEFAULT 0")
        if "scan_fee_usd" not in cols:
            alters.append("ALTER TABLE operations ADD COLUMN scan_fee_usd REAL DEFAULT 0")
        if "tool_fee_usd" not in cols:
            alters.append("ALTER TABLE operations ADD COLUMN tool_fee_usd REAL DEFAULT 0")
        if "tool_units" not in cols:
            alters.append("ALTER TABLE operations ADD COLUMN tool_units INTEGER DEFAULT 0")
        if "billable_total_usd" not in cols:
            alters.append("ALTER TABLE operations ADD COLUMN billable_total_usd REAL DEFAULT 0")
        if "pricing_breakdown_json" not in cols:
            alters.append("ALTER TABLE operations ADD COLUMN pricing_breakdown_json TEXT")
        for stmt in alters:
            sync_conn.execute(text(stmt))

    if insp.has_table("billing_sessions"):
        bcols = {c["name"] for c in insp.get_columns("billing_sessions")}
        balters: list[str] = []
        if "total_scan_fees_usd" not in bcols:
            balters.append("ALTER TABLE billing_sessions ADD COLUMN total_scan_fees_usd REAL DEFAULT 0")
        if "total_tool_fees_usd" not in bcols:
            balters.append("ALTER TABLE billing_sessions ADD COLUMN total_tool_fees_usd REAL DEFAULT 0")
        if "total_tool_units" not in bcols:
            balters.append("ALTER TABLE billing_sessions ADD COLUMN total_tool_units INTEGER DEFAULT 0")
        if "total_billable_usd" not in bcols:
            balters.append("ALTER TABLE billing_sessions ADD COLUMN total_billable_usd REAL DEFAULT 0")
        for stmt in balters:
            sync_conn.execute(text(stmt))
