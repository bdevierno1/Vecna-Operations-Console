from __future__ import annotations

from app.services.billing import _merge_model_usage
from app.services.cost import UsageSummary


def test_merge_model_usage_accumulates() -> None:
    u1 = UsageSummary(
        input_tokens=100,
        output_tokens=50,
        cache_read_tokens=0,
        cache_write_tokens=0,
        cost_usd=0.01,
        credits=2.0,
    )
    u2 = UsageSummary(
        input_tokens=200,
        output_tokens=80,
        cache_read_tokens=10,
        cache_write_tokens=0,
        cost_usd=0.02,
        credits=3.0,
    )
    m = _merge_model_usage({}, "openai/gpt-4o-mini", u1)
    m = _merge_model_usage(m, "openai/gpt-4o-mini", u2)
    g = m["openai/gpt-4o-mini"]
    assert g["inputTokens"] == 300
    assert g["outputTokens"] == 130
    assert g["costUSD"] == 0.03
