"""Position-sizing and risk-guard module.

Implements the Vecna Trading Risk-Management Policy and Fractional-Kelly
Position Sizing rules:

  Per-trade risk cap    equity × risk_pct (default 1 %; hard max 3 %)
  Portfolio-heat limit  total open risk ≤ 6 % of equity at all times
  Drawdown circuit-     at −15 % peak-to-trough → halve sizes;
    breaker             at −25 % → flatten all and halt trading
  Fractional Kelly      quarter-Kelly default, half-Kelly hard cap,
                        never full Kelly; f* = W − (1−W)/R
  Concentration cap     single-name ≤ 20 % of equity
  Correlation cap       at most 2 open positions with ρ > 0.7
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

# ---------------------------------------------------------------------------
# Policy constants
# ---------------------------------------------------------------------------

RISK_PCT_DEFAULT: float = 0.01       # 1 % per trade
RISK_PCT_MIN: float = 0.01           # floor
RISK_PCT_MAX: float = 0.03           # 3 % ceiling

PORTFOLIO_HEAT_LIMIT: float = 0.06   # 6 % total open risk

DD_HALVE_THRESHOLD: float = -0.15    # −15 % drawdown → halve sizes
DD_HALT_THRESHOLD: float = -0.25     # −25 % drawdown → flatten + halt

KELLY_DEFAULT_FRACTION: float = 0.25  # quarter-Kelly default
KELLY_MAX_FRACTION: float = 0.50      # half-Kelly hard cap

CONCENTRATION_CAP: float = 0.20       # 20 % single-name equity cap
CORRELATION_THRESHOLD: float = 0.70   # ρ > 0.7 counts as correlated
CORRELATION_MAX_POSITIONS: int = 2    # max correlated positions allowed


# ---------------------------------------------------------------------------
# Domain types
# ---------------------------------------------------------------------------


class CircuitBreakerState(str, Enum):
    """Drawdown-based trading state."""

    NORMAL = "normal"
    HALVED = "halved"   # −15 % drawdown: effective risk budget cut in half
    HALTED = "halted"   # −25 % drawdown: trading stopped, flatten all


@dataclass
class Position:
    """Snapshot of a single open position for risk calculations."""

    instrument: str
    entry_price: float
    stop_price: float
    shares: float           # units / shares / contracts
    equity_at_entry: float  # account equity when the position was sized

    @property
    def risk_dollars(self) -> float:
        """Dollar risk = |entry − stop| × shares."""
        return abs(self.entry_price - self.stop_price) * self.shares


@dataclass
class Portfolio:
    """Live portfolio state fed into every pre-trade check."""

    equity: float                               # current account equity ($)
    peak_equity: float                          # highest equity seen (for drawdown)
    open_positions: list[Position] = field(default_factory=list)
    # Pairwise correlation matrix keyed by frozenset of instrument names
    correlations: dict[frozenset[str], float] = field(default_factory=dict)

    @property
    def drawdown(self) -> float:
        """Peak-to-trough drawdown as a negative fraction (0.0 if peak ≤ 0)."""
        if self.peak_equity <= 0:
            return 0.0
        return (self.equity - self.peak_equity) / self.peak_equity

    @property
    def circuit_breaker_state(self) -> CircuitBreakerState:
        dd = self.drawdown
        if dd <= DD_HALT_THRESHOLD:
            return CircuitBreakerState.HALTED
        if dd <= DD_HALVE_THRESHOLD:
            return CircuitBreakerState.HALVED
        return CircuitBreakerState.NORMAL

    @property
    def total_open_risk(self) -> float:
        """Total dollar risk across all open positions."""
        return sum(p.risk_dollars for p in self.open_positions)

    @property
    def total_open_risk_pct(self) -> float:
        """Total open risk as a fraction of equity; 0.0 when equity ≤ 0."""
        if self.equity <= 0:
            return 0.0
        return self.total_open_risk / self.equity

    def exposure_for(self, instrument: str) -> float:
        """Notional dollar exposure (entry × shares) for the given instrument."""
        return sum(
            p.entry_price * p.shares
            for p in self.open_positions
            if p.instrument == instrument
        )

    def correlated_position_count(self, instrument: str) -> int:
        """Count open positions whose correlation with *instrument* exceeds the threshold."""
        count = 0
        for pos in self.open_positions:
            if pos.instrument == instrument:
                continue
            pair: frozenset[str] = frozenset({instrument, pos.instrument})
            if self.correlations.get(pair, 0.0) > CORRELATION_THRESHOLD:
                count += 1
        return count


@dataclass(frozen=True)
class SizeResult:
    """Output from :func:`compute_position_size`."""

    shares: float                   # final position size in units
    risk_dollars: float             # dollar risk for this trade
    kelly_fraction_used: float      # fractional-Kelly value applied (0.0 if not used)
    binding_rule: str               # "kelly" | "risk_cap" | "heat_limit" | "concentration"
    cb_state: CircuitBreakerState


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class RiskGuardError(Exception):
    """Raised when a risk guard blocks a proposed trade or portfolio action."""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def kelly_fraction(win_rate: float, payoff_ratio: float) -> float:
    """Compute the full Kelly fraction.

    Formula: f* = W − (1−W) / R

    Args:
        win_rate:      historical win rate, strictly in (0, 1).
        payoff_ratio:  average-win / average-loss ratio, > 0.

    Returns:
        Raw Kelly fraction; may be ≤ 0 (no positive edge).

    Raises:
        ValueError: if arguments are out of their valid ranges.
    """
    if not (0.0 < win_rate < 1.0):
        raise ValueError(f"win_rate must be in (0, 1); got {win_rate!r}")
    if payoff_ratio <= 0.0:
        raise ValueError(f"payoff_ratio must be > 0; got {payoff_ratio!r}")
    return win_rate - (1.0 - win_rate) / payoff_ratio


def fractional_kelly(
    win_rate: float,
    payoff_ratio: float,
    fraction: float = KELLY_DEFAULT_FRACTION,
) -> float:
    """Return the fractional-Kelly equity fraction to risk.

    Clamps the multiplier to ≤ ``KELLY_MAX_FRACTION`` (half-Kelly) and
    returns 0.0 when there is no positive edge.

    Args:
        win_rate:     historical win rate.
        payoff_ratio: average-win / average-loss.
        fraction:     Kelly multiplier (default 0.25; capped at 0.5).

    Returns:
        Fraction of equity to risk; always in [0.0, ``KELLY_MAX_FRACTION``].
    """
    fraction = min(fraction, KELLY_MAX_FRACTION)
    f_star = kelly_fraction(win_rate, payoff_ratio)
    if f_star <= 0.0:
        return 0.0
    return min(fraction * f_star, KELLY_MAX_FRACTION)


def compute_position_size(
    *,
    portfolio: Portfolio,
    instrument: str,
    entry_price: float,
    stop_price: float,
    risk_pct: float = RISK_PCT_DEFAULT,
    win_rate: float | None = None,
    payoff_ratio: float | None = None,
    kelly_fraction_override: float = KELLY_DEFAULT_FRACTION,
) -> SizeResult:
    """Compute the allowed position size subject to all active risk guards.

    Guard hierarchy (applied in order):
        1. Circuit-breaker — block trade (HALTED) or halve risk budget (HALVED).
        2. Per-trade risk cap — equity × effective_risk_pct.
        3. Fractional-Kelly cap — if win_rate and payoff_ratio are supplied.
        4. Take the *smaller* of risk-cap and Kelly-derived size.
        5. Portfolio-heat guard — trim to remaining heat budget.
        6. Single-name concentration cap — trim to remaining single-name budget.
        7. Correlation cap — hard block if already at the maximum.

    Args:
        portfolio:              current portfolio state.
        instrument:             ticker / instrument identifier.
        entry_price:            intended entry price.
        stop_price:             mechanical stop price. Required — no stop, no trade.
        risk_pct:               per-trade risk fraction (default 0.01; clamped to
                                [RISK_PCT_MIN, RISK_PCT_MAX]).
        win_rate:               historical win rate for Kelly sizing; None skips Kelly.
        payoff_ratio:           average-win / average-loss; required when win_rate given.
        kelly_fraction_override: Kelly multiplier (default 0.25; hard cap 0.5).

    Returns:
        :class:`SizeResult` with final share count and decision metadata.

    Raises:
        RiskGuardError: when a hard guard blocks the trade entirely.
        ValueError:     for invalid argument values.
    """
    if entry_price <= 0:
        raise ValueError(f"entry_price must be > 0; got {entry_price!r}")
    risk_per_share = abs(entry_price - stop_price)
    if risk_per_share == 0.0:
        raise RiskGuardError("stop_price equals entry_price — no stop defined; trade rejected")

    # 1. Circuit-breaker ------------------------------------------------
    cb_state = portfolio.circuit_breaker_state
    if cb_state == CircuitBreakerState.HALTED:
        raise RiskGuardError(
            f"Trading halted: drawdown {portfolio.drawdown:.1%} reached the "
            f"{DD_HALT_THRESHOLD:.0%} threshold. Flatten all positions and stop."
        )

    # 2. Clamp risk_pct, apply halve-modifier
    risk_pct = max(RISK_PCT_MIN, min(risk_pct, RISK_PCT_MAX))
    effective_risk_pct = risk_pct * (0.5 if cb_state == CircuitBreakerState.HALVED else 1.0)
    equity = portfolio.equity

    risk_dollars_cap = equity * effective_risk_pct
    shares_from_cap = risk_dollars_cap / risk_per_share

    # 3. Fractional-Kelly sizing ----------------------------------------
    kelly_frac_used = 0.0
    shares_from_kelly: float | None = None
    if win_rate is not None:
        if payoff_ratio is None:
            raise ValueError("payoff_ratio is required when win_rate is supplied")
        kelly_frac_used = fractional_kelly(win_rate, payoff_ratio, kelly_fraction_override)
        risk_dollars_kelly = equity * kelly_frac_used
        shares_from_kelly = risk_dollars_kelly / risk_per_share

    # 4. Take the smaller -------------------------------------------------
    if shares_from_kelly is not None and shares_from_kelly < shares_from_cap:
        shares = shares_from_kelly
        risk_dollars = equity * kelly_frac_used
        binding_rule = "kelly"
    else:
        shares = shares_from_cap
        risk_dollars = risk_dollars_cap
        binding_rule = "risk_cap"

    if shares <= 0.0:
        raise RiskGuardError(
            "Computed position size is zero — no positive edge or stop too tight"
        )

    # 5. Portfolio heat guard -------------------------------------------
    existing_heat = portfolio.total_open_risk
    new_heat_pct = (existing_heat + risk_dollars) / equity
    if new_heat_pct > PORTFOLIO_HEAT_LIMIT:
        available_dollars = max(0.0, PORTFOLIO_HEAT_LIMIT * equity - existing_heat)
        if available_dollars <= 0.0:
            raise RiskGuardError(
                f"Portfolio heat limit ({PORTFOLIO_HEAT_LIMIT:.0%}) already reached; "
                "no new risk capacity available."
            )
        shares = available_dollars / risk_per_share
        risk_dollars = available_dollars
        binding_rule = "heat_limit"

    # 6. Single-name concentration cap ----------------------------------
    existing_exposure = portfolio.exposure_for(instrument)
    new_exposure = existing_exposure + entry_price * shares
    if new_exposure / equity > CONCENTRATION_CAP:
        allowed_exposure = CONCENTRATION_CAP * equity - existing_exposure
        if allowed_exposure <= 0.0:
            raise RiskGuardError(
                f"Concentration cap ({CONCENTRATION_CAP:.0%}) already reached "
                f"for {instrument!r}."
            )
        shares = allowed_exposure / entry_price
        risk_dollars = shares * risk_per_share
        binding_rule = "concentration"

    # 7. Correlation cap ------------------------------------------------
    corr_count = portfolio.correlated_position_count(instrument)
    if corr_count >= CORRELATION_MAX_POSITIONS:
        raise RiskGuardError(
            f"Correlation cap: {corr_count} existing position(s) with ρ > "
            f"{CORRELATION_THRESHOLD} already open relative to {instrument!r}; "
            f"max is {CORRELATION_MAX_POSITIONS}."
        )

    return SizeResult(
        shares=shares,
        risk_dollars=risk_dollars,
        kelly_fraction_used=kelly_frac_used,
        binding_rule=binding_rule,
        cb_state=cb_state,
    )


def check_portfolio_heat(portfolio: Portfolio) -> None:
    """Assert current portfolio heat is within policy limits.

    Useful as a standalone pre-trade check independent of position sizing.

    Raises:
        RiskGuardError: if total open risk already exceeds the heat limit.
        ValueError:     if equity is not positive.
    """
    if portfolio.equity <= 0:
        raise ValueError("equity must be positive")
    heat_pct = portfolio.total_open_risk_pct
    if heat_pct > PORTFOLIO_HEAT_LIMIT:
        raise RiskGuardError(
            f"Portfolio heat {heat_pct:.2%} exceeds the "
            f"{PORTFOLIO_HEAT_LIMIT:.0%} limit."
        )
