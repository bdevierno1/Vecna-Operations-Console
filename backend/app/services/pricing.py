"""Billable pricing on top of LLM token cost: per successful scan + tiered tool fees.

Token $ stays in app.services.cost (UsageSummary.cost_usd). This layer adds:
  - Flat platform fee per completed operation (scan)
  - Tool fees: optional legacy flat $/call, or tiered families + per-tool overrides

Monthly tier caps are not enforced here — expose quotas via config/API later.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.services.cost import UsageSummary

if TYPE_CHECKING:
    from app.config import Settings

# Built-in families (extend via VECNA_TOOL_FAMILY_FEES_JSON).
DEFAULT_TOOL_FAMILY_FEES: dict[str, float] = {
    "default": 0.0,
    "dns": 0.0,
    "network": 0.0,
    "paths": 0.0,
    "premium": 0.0,
}

# Vecna recon tools → family (override via VECNA_TOOL_FAMILY_BY_TOOL_JSON).
DEFAULT_TOOL_FAMILY_BY_TOOL: dict[str, str] = {
    "resolve_dns_and_subdomains": "dns",
    "analyze_http_security_headers": "network",
    "probe_common_paths": "paths",
}


def _parse_json_obj(s: str) -> dict[str, Any]:
    try:
        raw = json.loads(s or "{}")
    except json.JSONDecodeError:
        return {}
    return raw if isinstance(raw, dict) else {}


def merged_tool_family_fees(settings: Settings) -> dict[str, float]:
    out = dict(DEFAULT_TOOL_FAMILY_FEES)
    for k, v in _parse_json_obj(settings.vecna_tool_family_fees_json).items():
        out[str(k)] = float(v)
    return out


def merged_tool_family_by_tool(settings: Settings) -> dict[str, str]:
    out = dict(DEFAULT_TOOL_FAMILY_BY_TOOL)
    for k, v in _parse_json_obj(settings.vecna_tool_family_by_tool_json).items():
        out[str(k)] = str(v)
    return out


def tool_fee_overrides_map(settings: Settings) -> dict[str, float]:
    return {str(k): float(v) for k, v in _parse_json_obj(settings.vecna_tool_fee_overrides_json).items()}


def has_tiered_tool_config(settings: Settings) -> bool:
    """True if any tiered JSON env var is non-empty (beyond `{}`)."""
    return bool(
        _parse_json_obj(settings.vecna_tool_family_fees_json)
        or _parse_json_obj(settings.vecna_tool_family_by_tool_json)
        or _parse_json_obj(settings.vecna_tool_fee_overrides_json)
    )


def tool_pricing_public_snapshot(settings: Settings) -> dict[str, Any]:
    """For GET /api/config — no secrets."""
    tiered = has_tiered_tool_config(settings)
    return {
        "tiered_config_active": tiered,
        "families": merged_tool_family_fees(settings),
        "tool_to_family": merged_tool_family_by_tool(settings),
        "overrides": tool_fee_overrides_map(settings),
        "legacy_flat_fee_per_call_usd": float(settings.vecna_tool_unit_fee_usd),
        "default_families": list(DEFAULT_TOOL_FAMILY_FEES.keys()),
    }


@dataclass
class BillableBreakdown:
    """One completed recon operation."""

    llm_cost_usd: float
    scan_fee_usd: float
    tool_fee_usd: float
    tool_units: int
    billable_total_usd: float
    tool_calls_by_tool: dict[str, int] = field(default_factory=dict)
    tool_fee_by_family: dict[str, float] = field(default_factory=dict)
    tool_fee_detail: list[dict[str, Any]] = field(default_factory=list)
    pricing_mode: str = "tiered"  # "tiered" | "legacy_flat"


def compute_tool_fees(
    usage: UsageSummary,
    settings: Settings,
) -> tuple[float, dict[str, float], list[dict[str, Any]], str]:
    """Returns (tool_fee_usd, fee_by_family, detail_rows, pricing_mode)."""
    tiered = has_tiered_tool_config(settings)
    legacy_rate = float(settings.vecna_tool_unit_fee_usd)
    counts = dict(usage.tool_calls_by_tool)
    total_calls = int(usage.tool_calls)

    fee_by_family: dict[str, float] = defaultdict(float)
    detail: list[dict[str, Any]] = []

    if not tiered and legacy_rate > 0.0:
        fee = legacy_rate * total_calls
        if fee:
            fee_by_family["legacy_flat"] = fee
        if counts:
            for tool, n in sorted(counts.items()):
                if n <= 0:
                    continue
                tf = legacy_rate * n
                detail.append(
                    {
                        "tool": tool,
                        "calls": n,
                        "fee_usd": round(tf, 6),
                        "family": None,
                        "pricing": "legacy_flat",
                    }
                )
        elif total_calls > 0:
            detail.append(
                {
                    "tool": "_aggregate",
                    "calls": total_calls,
                    "fee_usd": round(fee, 6),
                    "family": None,
                    "pricing": "legacy_flat",
                }
            )
        return fee, dict(fee_by_family), detail, "legacy_flat"

    # Tiered: per-tool families + overrides + default family rate for unknown tools.
    fees_map = merged_tool_family_fees(settings)
    by_tool = merged_tool_family_by_tool(settings)
    overrides = tool_fee_overrides_map(settings)
    default_rate = float(fees_map.get("default", 0.0))

    def add_row(
        tool: str,
        n: int,
        fee_part: float,
        fam: str | None,
        pricing: str,
    ) -> None:
        if n <= 0:
            return
        if fam:
            fee_by_family[fam] += fee_part
        detail.append(
            {
                "tool": tool,
                "calls": n,
                "fee_usd": round(fee_part, 6),
                "family": fam,
                "pricing": pricing,
            }
        )

    total_fee = 0.0
    if counts:
        for tool, n in sorted(counts.items()):
            if n <= 0:
                continue
            if tool in overrides:
                rate = overrides[tool]
                part = rate * n
                total_fee += part
                add_row(tool, n, part, "override", "override")
                continue
            fam = by_tool.get(tool, "default")
            rate = float(fees_map.get(fam, default_rate))
            part = rate * n
            total_fee += part
            add_row(tool, n, part, fam, "family")
    elif total_calls > 0:
        # Metrics had a total but no per-tool breakdown — bill default family only.
        part = default_rate * total_calls
        total_fee += part
        add_row("_aggregate", total_calls, part, "default", "default_family")

    return total_fee, dict(fee_by_family), detail, "tiered"


def billable_for_completed_scan(usage: UsageSummary, settings: Settings) -> BillableBreakdown:
    """Apply scan flat fee + tool fees on top of LLM usage (only for successful completions)."""
    llm = float(usage.cost_usd)
    scan = float(settings.vecna_scan_fee_usd)
    tool_fee, fee_by_fam, detail, mode = compute_tool_fees(usage, settings)
    units = int(usage.tool_calls)
    total = llm + scan + tool_fee
    return BillableBreakdown(
        llm_cost_usd=llm,
        scan_fee_usd=scan,
        tool_fee_usd=tool_fee,
        tool_units=units,
        billable_total_usd=total,
        tool_calls_by_tool=dict(usage.tool_calls_by_tool),
        tool_fee_by_family=fee_by_fam,
        tool_fee_detail=detail,
        pricing_mode=mode,
    )


def breakdown_to_dict(b: BillableBreakdown) -> dict[str, Any]:
    return {
        "llm_cost_usd": b.llm_cost_usd,
        "scan_fee_usd": b.scan_fee_usd,
        "tool_fee_usd": b.tool_fee_usd,
        "tool_units": b.tool_units,
        "billable_total_usd": b.billable_total_usd,
        "tool_calls_by_tool": dict(b.tool_calls_by_tool),
        "tool_fee_by_family": dict(b.tool_fee_by_family),
        "tool_fee_detail": list(b.tool_fee_detail),
        "pricing_mode": b.pricing_mode,
    }


def display_billable_total(
    *,
    llm_cost_usd: float,
    scan_fee_usd: float,
    tool_fee_usd: float,
    stored_billable_usd: float,
) -> float:
    """Prefer persisted total; if missing (pre-pricing rows), use LLM + fees."""
    if stored_billable_usd and stored_billable_usd > 0.0:
        return float(stored_billable_usd)
    return float(llm_cost_usd) + float(scan_fee_usd) + float(tool_fee_usd)
