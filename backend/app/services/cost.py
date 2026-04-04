"""Cost and token-usage tracking for LiteLLM-backed runs.

- ModelCosts: $/1M for input, output, prompt cache read, prompt cache write.
- LiteLLM cost_per_token first (supports cache_read_input_tokens / cache_creation_input_tokens).
- Static prefix table with full cache rates when LiteLLM cannot price the model.
- Unknown model: conservative tier + has_unknown_model_cost flag + telemetry.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from strands.telemetry.metrics import EventLoopMetrics

# ---------------------------------------------------------------------------
# Tiers — $ per 1M tokens
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ModelCosts:
    """$/1M tokens: input, output, cache read, cache write (prompt)."""

    input_per_mtok: float
    output_per_mtok: float
    cache_read_per_mtok: float
    cache_write_per_mtok: float


# Standard Sonnet-class tier: $3 / $15, cache read $0.30 / write $3.75 per Mtok
COST_TIER_3_15 = ModelCosts(3.0, 15.0, 0.3, 3.75)
# Opus 4 / 4.1 class
COST_TIER_15_75 = ModelCosts(15.0, 75.0, 1.5, 18.75)
# Opus 4.5 class
COST_TIER_5_25 = ModelCosts(5.0, 25.0, 0.5, 6.25)
# Haiku 3.5
COST_HAIKU_35 = ModelCosts(0.8, 4.0, 0.08, 1.0)
# Haiku 4.5
COST_HAIKU_45 = ModelCosts(1.0, 5.0, 0.1, 1.25)
# GPT-4o-mini–class (cheap)
COST_TIER_015_060 = ModelCosts(0.15, 0.6, 0.015, 0.075)

DEFAULT_UNKNOWN_MODEL_COST = COST_TIER_5_25

# Longest-prefix match wins (most specific first handled in _match_static_costs).
_PREFIX_COSTS: list[tuple[str, ModelCosts]] = [
    # OpenRouter — Claude family
    ("openrouter/anthropic/claude-3-5-haiku", COST_HAIKU_35),
    ("openrouter/anthropic/claude-3-5-sonnet", COST_TIER_3_15),
    ("openrouter/anthropic/claude-3-7-sonnet", COST_TIER_3_15),
    ("openrouter/anthropic/claude-sonnet-4", COST_TIER_3_15),
    ("openrouter/anthropic/claude-opus-4", COST_TIER_15_75),
    # Direct Claude API (same models as above, non–OpenRouter route)
    ("anthropic/claude-3-5-haiku", COST_HAIKU_35),
    ("anthropic/claude-3-5-sonnet", COST_TIER_3_15),
    ("anthropic/claude-3-7-sonnet", COST_TIER_3_15),
    ("anthropic/claude-sonnet-4", COST_TIER_3_15),
    ("anthropic/claude-opus-4", COST_TIER_15_75),
    # OpenAI
    ("openai/gpt-4o-mini", COST_TIER_015_060),
    ("openai/gpt-4o", ModelCosts(2.5, 10.0, 0.25, 1.25)),
    ("openai/o1", ModelCosts(15.0, 60.0, 1.5, 7.5)),
    ("openai/o3-mini", ModelCosts(1.1, 4.4, 0.11, 0.55)),
    ("openai/o3", ModelCosts(10.0, 40.0, 1.0, 5.0)),
    # OpenRouter — DeepSeek / Qwen / Google / Meta (approximate Sonnet/mini tiers)
    ("openrouter/deepseek/deepseek-r1", ModelCosts(0.55, 2.19, 0.06, 0.55)),
    ("openrouter/deepseek/deepseek-chat", ModelCosts(0.07, 1.1, 0.01, 0.07)),
    ("openrouter/qwen/qwq-32b", COST_TIER_015_060),
    ("openrouter/qwen/qwen-2.5-72b", ModelCosts(0.35, 0.4, 0.035, 0.35)),
    ("openrouter/google/gemini-2.5-pro", ModelCosts(1.25, 10.0, 0.125, 1.25)),
    ("openrouter/google/gemini-2.0-flash", ModelCosts(0.1, 0.4, 0.01, 0.1)),
    ("openrouter/meta-llama/llama-4-maverick", ModelCosts(0.18, 0.6, 0.02, 0.18)),
    ("openrouter/meta-llama/llama-3.3-70b", ModelCosts(0.12, 0.3, 0.012, 0.12)),
    # Gemini direct
    ("gemini/gemini-2.5-pro", ModelCosts(1.25, 10.0, 0.125, 1.25)),
    ("gemini/gemini-2.0-flash", ModelCosts(0.1, 0.4, 0.01, 0.1)),
]


def tokens_to_usd_cost(
    costs: ModelCosts,
    inp: int,
    out: int,
    cache_r: int,
    cache_w: int,
) -> float:
    """USD from four token buckets (input, output, cache read, cache write)."""
    return (
        (inp / 1_000_000) * costs.input_per_mtok
        + (out / 1_000_000) * costs.output_per_mtok
        + (cache_r / 1_000_000) * costs.cache_read_per_mtok
        + (cache_w / 1_000_000) * costs.cache_write_per_mtok
    )


def _match_static_model_costs(model_id: str) -> ModelCosts | None:
    for prefix, costs in sorted(_PREFIX_COSTS, key=lambda x: len(x[0]), reverse=True):
        if model_id.startswith(prefix):
            return costs
    return None


def _usd_from_litellm(
    model_id: str,
    inp: int,
    out: int,
    cache_r: int,
    cache_w: int,
) -> float | None:
    try:
        import litellm  # noqa: PLC0415

        prompt_c, completion_c = litellm.cost_per_token(
            model=model_id,
            prompt_tokens=inp,
            completion_tokens=out,
            cache_read_input_tokens=cache_r,
            cache_creation_input_tokens=cache_w,
        )
        return float(prompt_c + completion_c)
    except Exception:
        return None


@dataclass
class UsageSummary:
    """Rolling token/credit/cost totals for a session."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cycles: int = 0
    tool_calls: int = 0
    tool_calls_by_tool: dict[str, int] = field(default_factory=dict)
    cost_usd: float = 0.0
    credits: float = 0.0
    has_unknown_model_cost: bool = False


def calculate_cost_from_tokens(
    model_id: str,
    *,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> tuple[float, bool]:
    """Returns (usd, has_unknown_model_cost)."""
    u, unk = _compute_usd_and_unknown(model_id, input_tokens, output_tokens, cache_read_tokens, cache_write_tokens)
    return u, unk


def _compute_usd_and_unknown(
    model_id: str,
    inp: int,
    out: int,
    cache_r: int,
    cache_w: int,
) -> tuple[float, bool]:
    lit = _usd_from_litellm(model_id, inp, out, cache_r, cache_w)
    if lit is not None:
        return max(0.0, lit), False

    mc = _match_static_model_costs(model_id)
    if mc is not None:
        return tokens_to_usd_cost(mc, inp, out, cache_r, cache_w), False

    return tokens_to_usd_cost(DEFAULT_UNKNOWN_MODEL_COST, inp, out, cache_r, cache_w), True


def usage_from_metrics(model_id: str, m: EventLoopMetrics) -> UsageSummary:
    acc = m.accumulated_usage
    inp = int(acc.get("inputTokens") or 0)
    out = int(acc.get("outputTokens") or 0)
    cache_r = int(acc.get("cacheReadInputTokens") or 0)
    cache_w = int(acc.get("cacheWriteInputTokens") or 0)

    tool_calls_by_tool = {name: int(tm.call_count) for name, tm in m.tool_metrics.items()}
    tool_calls = sum(tool_calls_by_tool.values())
    cycles = m.cycle_count
    credits = tool_calls * 1.0 + cycles * 0.5
    cost_usd, unknown = _compute_usd_and_unknown(model_id, inp, out, cache_r, cache_w)

    return UsageSummary(
        input_tokens=inp,
        output_tokens=out,
        cache_read_tokens=cache_r,
        cache_write_tokens=cache_w,
        cycles=cycles,
        tool_calls=tool_calls,
        tool_calls_by_tool=tool_calls_by_tool,
        cost_usd=cost_usd,
        credits=credits,
        has_unknown_model_cost=unknown,
    )


def format_cost(cost_usd: float) -> str:
    if cost_usd > 0.5:
        return f"${cost_usd:.2f}"
    return f"${cost_usd:.4f}"


def format_model_pricing(costs: ModelCosts) -> str:
    """Input/output headline only ($/Mtok)."""
    def _p(x: float) -> str:
        return str(int(x)) if float(x).is_integer() else f"{x:.2f}"

    return f"${_p(costs.input_per_mtok)}/${_p(costs.output_per_mtok)} per Mtok"


def get_model_pricing_string(model_id: str) -> str | None:
    """From static table only; None if unknown."""
    mc = _match_static_model_costs(model_id)
    if mc is None:
        return None
    return format_model_pricing(mc)


def format_usage_summary(u: UsageSummary) -> str:
    """One-line human summary (cost, tokens, cycles, tools)."""
    tokens = f"{u.input_tokens:,} in / {u.output_tokens:,} out"
    cache = ""
    if u.cache_read_tokens or u.cache_write_tokens:
        cache = f" · {u.cache_read_tokens:,} cache-r / {u.cache_write_tokens:,} cache-w"
    unk = " (estimate)" if u.has_unknown_model_cost else ""
    return f"{format_cost(u.cost_usd)}{unk} · {tokens}{cache} · {u.cycles} cycle(s) · {u.tool_calls} tool call(s)"
