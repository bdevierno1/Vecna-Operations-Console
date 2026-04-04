"""Session-level billing: cumulative USD/tokens per client session, persisted in SQLite.

Session-scoped billing state (totals, per-model usage, persistence hooks),
without desktop project config — the database is the source of truth.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import BillingSession
from app.services.cost import UsageSummary, format_cost
from app.services.pricing import BillableBreakdown
from app.services.telemetry import log_session_billing_updated


def _merge_model_usage(existing: dict[str, Any], model_id: str, u: UsageSummary) -> dict[str, Any]:
    prev = existing.get(model_id) or {
        "inputTokens": 0,
        "outputTokens": 0,
        "cacheReadInputTokens": 0,
        "cacheWriteInputTokens": 0,
        "costUSD": 0.0,
    }
    existing[model_id] = {
        "inputTokens": int(prev.get("inputTokens", 0)) + u.input_tokens,
        "outputTokens": int(prev.get("outputTokens", 0)) + u.output_tokens,
        "cacheReadInputTokens": int(prev.get("cacheReadInputTokens", 0)) + u.cache_read_tokens,
        "cacheWriteInputTokens": int(prev.get("cacheWriteInputTokens", 0)) + u.cache_write_tokens,
        "costUSD": float(prev.get("costUSD", 0.0)) + u.cost_usd,
    }
    return existing


@dataclass
class SessionBillingSnapshot:
    session_id: str
    total_cost_usd: float  # LLM/token $ (same field name as before)
    total_scan_fees_usd: float
    total_tool_fees_usd: float
    total_tool_units: int
    total_billable_usd: float
    total_credits: float
    total_input_tokens: int
    total_output_tokens: int
    total_cache_read_tokens: int
    total_cache_write_tokens: int
    operation_count: int
    wall_time_seconds: float
    model_usage: dict[str, Any]

    def to_api_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "total_cost_usd": self.total_cost_usd,
            "total_scan_fees_usd": self.total_scan_fees_usd,
            "total_tool_fees_usd": self.total_tool_fees_usd,
            "total_tool_units": self.total_tool_units,
            "total_billable_usd": self.total_billable_usd,
            "total_credits": self.total_credits,
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_cache_read_tokens": self.total_cache_read_tokens,
            "total_cache_write_tokens": self.total_cache_write_tokens,
            "operation_count": self.operation_count,
            "wall_time_seconds": self.wall_time_seconds,
            "model_usage": self.model_usage,
        }


def format_session_billing_text(s: SessionBillingSnapshot) -> str:
    """CLI-style block: session total + per-model lines."""
    lines = [
        f"Session billable: {format_cost(s.total_billable_usd)} "
        f"(LLM {format_cost(s.total_cost_usd)} · scan {format_cost(s.total_scan_fees_usd)} "
        f"· tool {format_cost(s.total_tool_fees_usd)} · {s.total_tool_units} tool units)",
        f"  Tokens: {s.total_input_tokens:,} in / {s.total_output_tokens:,} out",
        f"  Operations: {s.operation_count} · Credits (legacy): {s.total_credits:.2f}",
        f"  Wall time (agent runs): {s.wall_time_seconds:.1f}s",
        "  By model:",
    ]
    for model, u in sorted(s.model_usage.items()):
        lines.append(
            f"    {model}: {u.get('inputTokens', 0):,} in, {u.get('outputTokens', 0):,} out "
            f"({format_cost(float(u.get('costUSD', 0.0)))})"
        )
    return "\n".join(lines)


async def get_session_snapshot(
    session_factory: async_sessionmaker[AsyncSession],
    session_id: str,
) -> SessionBillingSnapshot | None:
    async with session_factory() as session:
        row = await session.get(BillingSession, session_id)
        if not row:
            return None
        llm = row.total_cost_usd or 0.0
        scan = row.total_scan_fees_usd or 0.0
        tool_f = row.total_tool_fees_usd or 0.0
        tool_u = row.total_tool_units or 0
        bill = row.total_billable_usd or 0.0
        if bill == 0.0 and scan == 0.0 and tool_f == 0.0 and llm > 0.0:
            bill = llm  # legacy sessions before billable columns were accumulated

        return SessionBillingSnapshot(
            session_id=row.id,
            total_cost_usd=llm,
            total_scan_fees_usd=scan,
            total_tool_fees_usd=tool_f,
            total_tool_units=tool_u,
            total_billable_usd=bill,
            total_credits=row.total_credits or 0.0,
            total_input_tokens=row.total_input_tokens or 0,
            total_output_tokens=row.total_output_tokens or 0,
            total_cache_read_tokens=row.total_cache_read_tokens or 0,
            total_cache_write_tokens=row.total_cache_write_tokens or 0,
            operation_count=row.operation_count or 0,
            wall_time_seconds=row.wall_time_seconds or 0.0,
            model_usage=dict(row.model_usage_json or {}),
        )


async def record_operation_for_session(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    billing_session_id: str,
    model_id: str,
    usage: UsageSummary,
    wall_seconds: float,
    billable: BillableBreakdown,
) -> SessionBillingSnapshot:
    """Accumulate one completed operation into a billing session and persist."""
    async with session_factory() as session:
        row = await session.get(BillingSession, billing_session_id)
        if row is None:
            row = BillingSession(id=billing_session_id)
            session.add(row)

        row.total_cost_usd = (row.total_cost_usd or 0.0) + usage.cost_usd
        row.total_scan_fees_usd = (row.total_scan_fees_usd or 0.0) + billable.scan_fee_usd
        row.total_tool_fees_usd = (row.total_tool_fees_usd or 0.0) + billable.tool_fee_usd
        row.total_tool_units = (row.total_tool_units or 0) + billable.tool_units
        row.total_billable_usd = (row.total_billable_usd or 0.0) + billable.billable_total_usd
        row.total_credits = (row.total_credits or 0.0) + usage.credits
        row.total_input_tokens = (row.total_input_tokens or 0) + usage.input_tokens
        row.total_output_tokens = (row.total_output_tokens or 0) + usage.output_tokens
        row.total_cache_read_tokens = (row.total_cache_read_tokens or 0) + usage.cache_read_tokens
        row.total_cache_write_tokens = (row.total_cache_write_tokens or 0) + usage.cache_write_tokens
        row.operation_count = (row.operation_count or 0) + 1
        row.wall_time_seconds = (row.wall_time_seconds or 0.0) + wall_seconds
        mu = _merge_model_usage(dict(row.model_usage_json or {}), model_id, usage)
        row.model_usage_json = mu
        session.add(row)
        await session.commit()
        await session.refresh(row)

        llm = row.total_cost_usd or 0.0
        scan = row.total_scan_fees_usd or 0.0
        tool_f = row.total_tool_fees_usd or 0.0
        tool_u = row.total_tool_units or 0
        bill = row.total_billable_usd or 0.0
        snap = SessionBillingSnapshot(
            session_id=row.id,
            total_cost_usd=llm,
            total_scan_fees_usd=scan,
            total_tool_fees_usd=tool_f,
            total_tool_units=tool_u,
            total_billable_usd=bill,
            total_credits=row.total_credits or 0.0,
            total_input_tokens=row.total_input_tokens or 0,
            total_output_tokens=row.total_output_tokens or 0,
            total_cache_read_tokens=row.total_cache_read_tokens or 0,
            total_cache_write_tokens=row.total_cache_write_tokens or 0,
            operation_count=row.operation_count or 0,
            wall_time_seconds=row.wall_time_seconds or 0.0,
            model_usage=dict(row.model_usage_json or {}),
        )

    log_session_billing_updated(
        session_id=billing_session_id,
        total_billable_usd=snap.total_billable_usd,
        total_cost_usd=snap.total_cost_usd,
        operation_count=snap.operation_count,
    )
    return snap


async def create_billing_session(session_factory: async_sessionmaker[AsyncSession], session_id: str) -> None:
    async with session_factory() as session:
        existing = await session.get(BillingSession, session_id)
        if existing:
            return
        session.add(BillingSession(id=session_id))
        await session.commit()
