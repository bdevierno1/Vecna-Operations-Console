"""Unit tests for app.services.risk_guard.

Coverage targets:
  - kelly_fraction formula correctness
  - fractional_kelly: default quarter-Kelly, half-Kelly cap, no negative output
  - compute_position_size: risk-cap sizing, Kelly sizing, binding-rule selection
  - Drawdown circuit-breaker: NORMAL / HALVED / HALTED states
  - Portfolio heat guard: trim and hard block
  - Single-name concentration cap: trim and hard block
  - Correlation cap: block at limit
  - Edge cases: zero risk_per_share, equity = 0 in heat check
"""

from __future__ import annotations

import pytest

from app.services.risk_guard import (
    CORRELATION_MAX_POSITIONS,
    CORRELATION_THRESHOLD,
    DD_HALT_THRESHOLD,
    DD_HALVE_THRESHOLD,
    KELLY_DEFAULT_FRACTION,
    KELLY_MAX_FRACTION,
    PORTFOLIO_HEAT_LIMIT,
    RISK_PCT_DEFAULT,
    CircuitBreakerState,
    Portfolio,
    Position,
    RiskGuardError,
    SizeResult,
    check_portfolio_heat,
    compute_position_size,
    fractional_kelly,
    kelly_fraction,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _clean_portfolio(equity: float = 100_000.0) -> Portfolio:
    """Return an empty, healthy portfolio with equity == peak_equity."""
    return Portfolio(equity=equity, peak_equity=equity)


def _sized_position(
    instrument: str = "AAPL",
    entry: float = 100.0,
    stop: float = 95.0,
    shares: float = 10.0,
    equity: float = 100_000.0,
) -> Position:
    return Position(
        instrument=instrument,
        entry_price=entry,
        stop_price=stop,
        shares=shares,
        equity_at_entry=equity,
    )


# ---------------------------------------------------------------------------
# kelly_fraction
# ---------------------------------------------------------------------------


def test_kelly_fraction_breakeven_edge() -> None:
    # W=0.5, R=1.0 → f* = 0.5 - 0.5/1.0 = 0.0
    assert kelly_fraction(0.5, 1.0) == pytest.approx(0.0)


def test_kelly_fraction_positive_edge() -> None:
    # W=0.6, R=2.0 → f* = 0.6 - 0.4/2.0 = 0.6 - 0.2 = 0.4
    assert kelly_fraction(0.6, 2.0) == pytest.approx(0.4)


def test_kelly_fraction_negative_edge() -> None:
    # W=0.3, R=1.0 → f* = 0.3 - 0.7 = -0.4 (no edge)
    assert kelly_fraction(0.3, 1.0) == pytest.approx(-0.4)


def test_kelly_fraction_bad_win_rate() -> None:
    with pytest.raises(ValueError, match="win_rate"):
        kelly_fraction(0.0, 2.0)
    with pytest.raises(ValueError, match="win_rate"):
        kelly_fraction(1.0, 2.0)


def test_kelly_fraction_bad_payoff() -> None:
    with pytest.raises(ValueError, match="payoff_ratio"):
        kelly_fraction(0.5, 0.0)


# ---------------------------------------------------------------------------
# fractional_kelly
# ---------------------------------------------------------------------------


def test_fractional_kelly_default_quarter() -> None:
    # f* = 0.4; quarter-Kelly → 0.1
    result = fractional_kelly(0.6, 2.0)
    assert result == pytest.approx(0.4 * KELLY_DEFAULT_FRACTION)


def test_fractional_kelly_no_edge_returns_zero() -> None:
    result = fractional_kelly(0.3, 1.0)  # f* < 0
    assert result == 0.0


def test_fractional_kelly_capped_at_half_kelly() -> None:
    # Passing fraction=1.0 (full Kelly) must be clamped down to KELLY_MAX_FRACTION=0.5.
    # Since f* < 1.0 always, the final result is KELLY_MAX_FRACTION × f*, not
    # KELLY_MAX_FRACTION itself — but it must equal what you'd get with fraction=0.5.
    result_full = fractional_kelly(0.6, 2.0, fraction=1.0)
    result_half = fractional_kelly(0.6, 2.0, fraction=KELLY_MAX_FRACTION)
    assert result_full == pytest.approx(result_half)
    # And the result must never exceed KELLY_MAX_FRACTION
    assert result_full <= KELLY_MAX_FRACTION


def test_fractional_kelly_respects_max_fraction() -> None:
    # fraction arg already above cap — must be clamped
    result = fractional_kelly(0.9, 5.0, fraction=0.9)
    assert result <= KELLY_MAX_FRACTION


# ---------------------------------------------------------------------------
# compute_position_size — basic risk-cap sizing
# ---------------------------------------------------------------------------


def test_risk_cap_binding() -> None:
    # equity=100k, risk_pct=1%, entry=100, stop=95 → risk$=1000, shares=200
    portfolio = _clean_portfolio()
    result = compute_position_size(
        portfolio=portfolio,
        instrument="AAPL",
        entry_price=100.0,
        stop_price=95.0,
    )
    assert result.binding_rule == "risk_cap"
    assert result.shares == pytest.approx(200.0)
    assert result.risk_dollars == pytest.approx(1_000.0)
    assert result.cb_state == CircuitBreakerState.NORMAL


def test_kelly_beats_risk_cap() -> None:
    # f*=0.4, quarter→0.1 → risk$=10k, shares=2000 > 200 from risk cap
    # risk_cap (1%) = 1000 → 200 shares; Kelly (10%) = 10000 → 2000 shares
    # risk cap is smaller → binding_rule should be risk_cap
    portfolio = _clean_portfolio()
    result = compute_position_size(
        portfolio=portfolio,
        instrument="AAPL",
        entry_price=100.0,
        stop_price=95.0,
        win_rate=0.6,
        payoff_ratio=2.0,
    )
    # Kelly would give 2000 shares; risk cap gives 200 — risk cap wins
    assert result.binding_rule == "risk_cap"
    assert result.shares == pytest.approx(200.0)


def test_kelly_smaller_than_risk_cap() -> None:
    # Force Kelly to be binding: high risk_pct + small Kelly fraction
    # risk_pct=3%, equity=100k → cap=3000/share; Kelly: f*=0.4 at 0.05 frac → 0.02 → 2000
    portfolio = _clean_portfolio(equity=100_000.0)
    result = compute_position_size(
        portfolio=portfolio,
        instrument="SPY",
        entry_price=100.0,
        stop_price=90.0,    # risk_per_share=10
        risk_pct=0.03,       # 3k risk → 300 shares from cap
        win_rate=0.6,
        payoff_ratio=2.0,
        kelly_fraction_override=0.05,  # very small Kelly fraction → 0.02 of equity → 2k → 200 shares
    )
    # Kelly: 0.4 * 0.05 = 0.02 of equity → 2000$ → 200 shares
    # Cap: 3% of 100k = 3000$ → 300 shares
    # Kelly is smaller → kelly binding
    assert result.binding_rule == "kelly"
    assert result.shares == pytest.approx(200.0)


def test_no_stop_raises() -> None:
    portfolio = _clean_portfolio()
    with pytest.raises(RiskGuardError, match="no stop"):
        compute_position_size(
            portfolio=portfolio,
            instrument="AAPL",
            entry_price=100.0,
            stop_price=100.0,
        )


def test_invalid_entry_price() -> None:
    portfolio = _clean_portfolio()
    with pytest.raises(ValueError, match="entry_price"):
        compute_position_size(
            portfolio=portfolio,
            instrument="AAPL",
            entry_price=0.0,
            stop_price=95.0,
        )


def test_win_rate_without_payoff_raises() -> None:
    portfolio = _clean_portfolio()
    with pytest.raises(ValueError, match="payoff_ratio"):
        compute_position_size(
            portfolio=portfolio,
            instrument="AAPL",
            entry_price=100.0,
            stop_price=95.0,
            win_rate=0.6,
            # payoff_ratio omitted
        )


# ---------------------------------------------------------------------------
# Circuit-breaker states
# ---------------------------------------------------------------------------


def test_circuit_breaker_normal() -> None:
    # 10% drawdown: NORMAL
    p = Portfolio(equity=90_000.0, peak_equity=100_000.0)
    assert p.circuit_breaker_state == CircuitBreakerState.NORMAL
    assert p.drawdown == pytest.approx(-0.10)


def test_circuit_breaker_halved() -> None:
    # −20 % drawdown: HALVED
    p = Portfolio(equity=80_000.0, peak_equity=100_000.0)
    assert p.circuit_breaker_state == CircuitBreakerState.HALVED


def test_circuit_breaker_halted() -> None:
    # −30 % drawdown: HALTED
    p = Portfolio(equity=70_000.0, peak_equity=100_000.0)
    assert p.circuit_breaker_state == CircuitBreakerState.HALTED


def test_halted_circuit_breaker_blocks_trade() -> None:
    p = Portfolio(equity=70_000.0, peak_equity=100_000.0)
    with pytest.raises(RiskGuardError, match="halted|halve|flatten"):
        compute_position_size(
            portfolio=p,
            instrument="AAPL",
            entry_price=100.0,
            stop_price=95.0,
        )


def test_halved_circuit_breaker_cuts_size_in_half() -> None:
    # −20 % drawdown: effective risk_pct should be 0.5 %
    p = Portfolio(equity=80_000.0, peak_equity=100_000.0)
    result = compute_position_size(
        portfolio=p,
        instrument="AAPL",
        entry_price=100.0,
        stop_price=95.0,  # risk_per_share=5
    )
    # effective_risk_pct = 0.01 * 0.5 = 0.005; risk$ = 80k * 0.005 = 400
    # shares = 400 / 5 = 80
    assert result.cb_state == CircuitBreakerState.HALVED
    assert result.shares == pytest.approx(80.0)
    assert result.risk_dollars == pytest.approx(400.0)


# ---------------------------------------------------------------------------
# Portfolio heat guard
# ---------------------------------------------------------------------------


def test_portfolio_heat_trims_size() -> None:
    # Fill 5% heat, try to add 2% → trimmed to 1%
    equity = 100_000.0
    existing_risk = equity * 0.05   # 5 000$
    # One position: entry=100, stop=90, risk_per_share=10
    shares_existing = existing_risk / 10.0
    p = Portfolio(
        equity=equity,
        peak_equity=equity,
        open_positions=[
            Position("X", 100.0, 90.0, shares_existing, equity)
        ],
    )
    result = compute_position_size(
        portfolio=p,
        instrument="Y",
        entry_price=50.0,
        stop_price=40.0,   # risk_per_share=10
        risk_pct=0.02,
    )
    assert result.binding_rule == "heat_limit"
    # Available heat = (6% - 5%) × 100k = 1000$; shares = 1000/10 = 100
    assert result.risk_dollars == pytest.approx(1_000.0)
    assert result.shares == pytest.approx(100.0)


def test_portfolio_heat_full_blocks_trade() -> None:
    equity = 100_000.0
    risk_per_share = 5.0
    # 6% of equity as existing risk
    shares_existing = (equity * PORTFOLIO_HEAT_LIMIT) / risk_per_share
    p = Portfolio(
        equity=equity,
        peak_equity=equity,
        open_positions=[Position("Z", 100.0, 95.0, shares_existing, equity)],
    )
    with pytest.raises(RiskGuardError, match="heat limit"):
        compute_position_size(
            portfolio=p,
            instrument="NEW",
            entry_price=100.0,
            stop_price=95.0,
        )


def test_check_portfolio_heat_ok() -> None:
    p = _clean_portfolio()
    check_portfolio_heat(p)  # no exception


def test_check_portfolio_heat_exceeded() -> None:
    equity = 100_000.0
    p = Portfolio(
        equity=equity,
        peak_equity=equity,
        open_positions=[Position("A", 100.0, 90.0, 700.0, equity)],
    )
    # 700 * 10 = 7000 = 7% > 6%
    with pytest.raises(RiskGuardError, match="heat"):
        check_portfolio_heat(p)


def test_check_portfolio_heat_bad_equity() -> None:
    p = Portfolio(equity=0.0, peak_equity=0.0)
    with pytest.raises(ValueError, match="equity"):
        check_portfolio_heat(p)


# ---------------------------------------------------------------------------
# Single-name concentration cap
# ---------------------------------------------------------------------------


def test_concentration_cap_trims_size() -> None:
    equity = 100_000.0
    # Existing 15% exposure to AAPL: 15k at entry=100 → 150 shares
    existing_shares = (equity * 0.15) / 100.0
    p = Portfolio(
        equity=equity,
        peak_equity=equity,
        open_positions=[Position("AAPL", 100.0, 90.0, existing_shares, equity)],
    )
    # Requesting another batch of AAPL; cap is 20% → only 5k more allowed
    result = compute_position_size(
        portfolio=p,
        instrument="AAPL",
        entry_price=100.0,
        stop_price=90.0,
        risk_pct=0.03,
    )
    # Max additional exposure = 5k → 50 shares
    assert result.binding_rule == "concentration"
    assert result.shares == pytest.approx(50.0, rel=1e-4)


def test_concentration_cap_hard_block() -> None:
    equity = 100_000.0
    # Already at 20% exposure
    existing_shares = (equity * 0.20) / 100.0
    p = Portfolio(
        equity=equity,
        peak_equity=equity,
        open_positions=[Position("TSLA", 100.0, 90.0, existing_shares, equity)],
    )
    with pytest.raises(RiskGuardError, match="[Cc]oncentration"):
        compute_position_size(
            portfolio=p,
            instrument="TSLA",
            entry_price=100.0,
            stop_price=90.0,
        )


# ---------------------------------------------------------------------------
# Correlation cap
# ---------------------------------------------------------------------------


def test_correlation_cap_blocks_third_correlated_position() -> None:
    equity = 100_000.0
    # Two existing positions both highly correlated with the new instrument
    p = Portfolio(
        equity=equity,
        peak_equity=equity,
        open_positions=[
            Position("SPY", 400.0, 390.0, 5.0, equity),
            Position("IVV", 401.0, 391.0, 5.0, equity),
        ],
        correlations={
            frozenset({"QQQ", "SPY"}): 0.92,
            frozenset({"QQQ", "IVV"}): 0.88,
        },
    )
    with pytest.raises(RiskGuardError, match="[Cc]orrelation"):
        compute_position_size(
            portfolio=p,
            instrument="QQQ",
            entry_price=350.0,
            stop_price=340.0,
        )


def test_correlation_cap_allows_second_correlated_position() -> None:
    equity = 100_000.0
    # Only one existing correlated position → still allowed
    p = Portfolio(
        equity=equity,
        peak_equity=equity,
        open_positions=[
            Position("SPY", 400.0, 390.0, 5.0, equity),
        ],
        correlations={
            frozenset({"QQQ", "SPY"}): 0.92,
        },
    )
    result = compute_position_size(
        portfolio=p,
        instrument="QQQ",
        entry_price=350.0,
        stop_price=340.0,
    )
    assert isinstance(result, SizeResult)


def test_low_correlation_not_counted() -> None:
    equity = 100_000.0
    p = Portfolio(
        equity=equity,
        peak_equity=equity,
        open_positions=[
            Position("GLD", 200.0, 190.0, 5.0, equity),
            Position("BTC", 60_000.0, 58_000.0, 0.01, equity),
        ],
        correlations={
            frozenset({"AAPL", "GLD"}): 0.10,
            frozenset({"AAPL", "BTC"}): 0.35,
        },
    )
    # Neither is above CORRELATION_THRESHOLD → count = 0 → allowed
    result = compute_position_size(
        portfolio=p,
        instrument="AAPL",
        entry_price=150.0,
        stop_price=145.0,
    )
    assert isinstance(result, SizeResult)


# ---------------------------------------------------------------------------
# risk_pct clamping
# ---------------------------------------------------------------------------


def test_risk_pct_clamped_to_max() -> None:
    # risk_pct=10% should be silently clamped to 3%.
    # Use a low entry price so the concentration cap (20% of equity) doesn't bind:
    #   600 shares × $10 = $6 000 = 6 % of $100 k — well under the 20 % cap.
    portfolio = _clean_portfolio()
    result = compute_position_size(
        portfolio=portfolio,
        instrument="AAPL",
        entry_price=10.0,
        stop_price=5.0,   # risk_per_share = 5
        risk_pct=0.10,
    )
    # Clamped to 3 %: 3 000 / 5 = 600 shares
    assert result.binding_rule == "risk_cap"
    assert result.shares == pytest.approx(600.0)


def test_risk_pct_clamped_to_min() -> None:
    # risk_pct=0.001% should be clamped up to 1%
    portfolio = _clean_portfolio()
    result = compute_position_size(
        portfolio=portfolio,
        instrument="AAPL",
        entry_price=100.0,
        stop_price=95.0,
        risk_pct=0.0001,
    )
    assert result.shares == pytest.approx(200.0)  # 1% of 100k / 5
