from __future__ import annotations

from types import SimpleNamespace

from app.services.cost import UsageSummary
from app.services.pricing import billable_for_completed_scan, display_billable_total, has_tiered_tool_config


def _legacy_settings(**kwargs: object) -> SimpleNamespace:
    base = {
        "vecna_scan_fee_usd": 0.0,
        "vecna_tool_unit_fee_usd": 0.0,
        "vecna_tool_family_fees_json": "{}",
        "vecna_tool_family_by_tool_json": "{}",
        "vecna_tool_fee_overrides_json": "{}",
    }
    base.update(kwargs)
    return SimpleNamespace(**base)


def test_billable_legacy_flat_when_no_tiered_json() -> None:
    settings = _legacy_settings(vecna_scan_fee_usd=0.25, vecna_tool_unit_fee_usd=0.01)
    u = UsageSummary(
        input_tokens=100,
        output_tokens=50,
        cost_usd=0.02,
        tool_calls=3,
        tool_calls_by_tool={"a": 1, "b": 2},
    )
    b = billable_for_completed_scan(u, settings)
    assert b.llm_cost_usd == 0.02
    assert b.scan_fee_usd == 0.25
    assert b.tool_units == 3
    assert b.tool_fee_usd == 0.03
    assert b.pricing_mode == "legacy_flat"
    assert b.billable_total_usd == 0.02 + 0.25 + 0.03


def test_tiered_family_rates() -> None:
    settings = _legacy_settings(
        vecna_tool_family_fees_json='{"dns": 0.01, "network": 0.02}',
    )
    assert has_tiered_tool_config(settings) is True
    u = UsageSummary(
        cost_usd=0.01,
        tool_calls=3,
        tool_calls_by_tool={"resolve_dns_and_subdomains": 1, "analyze_http_security_headers": 2},
    )
    b = billable_for_completed_scan(u, settings)
    assert b.pricing_mode == "tiered"
    assert b.tool_fee_usd == 0.01 * 1 + 0.02 * 2
    assert b.tool_fee_by_family.get("dns") == 0.01
    assert b.tool_fee_by_family.get("network") == 0.04


def test_override_beats_family() -> None:
    settings = _legacy_settings(
        vecna_tool_family_fees_json='{"dns": 0.01}',
        vecna_tool_fee_overrides_json='{"resolve_dns_and_subdomains": 0.5}',
    )
    u = UsageSummary(
        cost_usd=0.0,
        tool_calls=1,
        tool_calls_by_tool={"resolve_dns_and_subdomains": 1},
    )
    b = billable_for_completed_scan(u, settings)
    assert b.tool_fee_usd == 0.5
    assert b.tool_fee_detail[0]["pricing"] == "override"


def test_tiered_ignores_legacy_flat_rate() -> None:
    settings = _legacy_settings(
        vecna_tool_unit_fee_usd=0.99,
        vecna_tool_family_fees_json='{"dns": 0.01}',
    )
    u = UsageSummary(
        tool_calls=1,
        tool_calls_by_tool={"resolve_dns_and_subdomains": 1},
    )
    b = billable_for_completed_scan(u, settings)
    assert b.tool_fee_usd == 0.01


def test_display_billable_total_prefers_stored() -> None:
    assert (
        display_billable_total(
            llm_cost_usd=1.0,
            scan_fee_usd=0.5,
            tool_fee_usd=0.1,
            stored_billable_usd=1.61,
        )
        == 1.61
    )


def test_display_billable_total_recomputes_when_stored_zero() -> None:
    assert (
        display_billable_total(
            llm_cost_usd=0.05,
            scan_fee_usd=0.0,
            tool_fee_usd=0.0,
            stored_billable_usd=0.0,
        )
        == 0.05
    )
