from __future__ import annotations

from app.services.cost import (
    COST_TIER_3_15,
    calculate_cost_from_tokens,
    tokens_to_usd_cost,
)


def test_tokens_to_usd_cost_includes_cache() -> None:
    # 1M input @ $3, 1M cache read @ $0.30
    u = tokens_to_usd_cost(COST_TIER_3_15, 1_000_000, 0, 1_000_000, 0)
    assert abs(u - 3.3) < 0.001


def test_calculate_cost_from_tokens_known_prefix() -> None:
    usd, unk = calculate_cost_from_tokens(
        "openrouter/anthropic/claude-3-5-sonnet",
        input_tokens=1_000_000,
        output_tokens=0,
        cache_read_tokens=0,
        cache_write_tokens=0,
    )
    assert not unk
    assert 2.9 < usd < 3.1


def test_calculate_cost_from_tokens_unknown() -> None:
    usd, unk = calculate_cost_from_tokens(
        "openrouter/unknown-vendor/unknown-model-xyz",
        input_tokens=1_000_000,
        output_tokens=0,
        cache_read_tokens=0,
        cache_write_tokens=0,
    )
    assert unk
    assert usd > 0
