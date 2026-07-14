"""
Data-quality check primitives.

Dimensions covered (per data-quality-dimensions.md):
  completeness — null rate on NOT-NULL cols < 0.5 %
  validity     — zero constraint violations (allowed values, non-negative)
  uniqueness   — zero duplicate PKs
  freshness    — latest row age ≤ SLA window (per pipeline-sla-and-freshness.md)

All functions accept a *synchronous* sqlalchemy.engine.Connection so they
compose cleanly in tests (sqlite:///:memory:) and in the CLI runner.
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from typing import Any

import sqlalchemy as sa


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class CheckResult:
    dataset: str
    check: str          # completeness | validity | uniqueness | freshness
    passed: bool
    detail: str
    value: Any = None
    threshold: Any = None

    def __str__(self) -> str:
        status = "PASS" if self.passed else "FAIL"
        return f"[{status}] {self.dataset}.{self.check}: {self.detail}"


# ---------------------------------------------------------------------------
# Completeness
# ---------------------------------------------------------------------------

def check_completeness(
    conn: sa.engine.Connection,
    table: str,
    not_null_cols: list[str],
    threshold_pct: float = 0.5,
) -> list[CheckResult]:
    """Null rate on each NOT-NULL column must be < threshold_pct %."""
    results: list[CheckResult] = []
    total: int = conn.execute(sa.text(f"SELECT COUNT(*) FROM {table}")).scalar()  # type: ignore[assignment]
    if total == 0:
        for col in not_null_cols:
            results.append(CheckResult(
                dataset=table, check="completeness", passed=True,
                detail=f"{col}: empty table — skipped",
                value=0.0, threshold=threshold_pct,
            ))
        return results

    for col in not_null_cols:
        null_count: int = conn.execute(  # type: ignore[assignment]
            sa.text(f"SELECT COUNT(*) FROM {table} WHERE {col} IS NULL")
        ).scalar()
        null_pct = (null_count / total) * 100
        passed = null_pct < threshold_pct
        results.append(CheckResult(
            dataset=table, check="completeness", passed=passed,
            detail=f"{col}: null_rate={null_pct:.4f}% (threshold<{threshold_pct}%)",
            value=round(null_pct, 6),
            threshold=threshold_pct,
        ))
    return results


# ---------------------------------------------------------------------------
# Uniqueness
# ---------------------------------------------------------------------------

def check_uniqueness(
    conn: sa.engine.Connection,
    table: str,
    primary_key: str,
) -> CheckResult:
    """Zero duplicate values on the primary-key column."""
    total: int = conn.execute(sa.text(f"SELECT COUNT(*) FROM {table}")).scalar()  # type: ignore[assignment]
    distinct: int = conn.execute(  # type: ignore[assignment]
        sa.text(f"SELECT COUNT(DISTINCT {primary_key}) FROM {table}")
    ).scalar()
    duplicates = total - distinct
    return CheckResult(
        dataset=table, check="uniqueness",
        passed=(duplicates == 0),
        detail=f"{primary_key}: {duplicates} duplicate(s) in {total} rows",
        value=duplicates,
        threshold=0,
    )


# ---------------------------------------------------------------------------
# Validity
# ---------------------------------------------------------------------------

def check_validity(
    conn: sa.engine.Connection,
    table: str,
    rules: list[dict[str, Any]],
) -> list[CheckResult]:
    """Zero schema violations.

    Supported rule types:
      {"type": "allowed_values", "col": "status", "allowed": ["a", "b"]}
      {"type": "non_negative",   "col": "cost_usd"}
    """
    results: list[CheckResult] = []
    for rule in rules:
        rtype = rule.get("type", "allowed_values")
        col = rule["col"]

        if rtype == "allowed_values":
            quoted = ", ".join(f"'{v}'" for v in rule["allowed"])
            n: int = conn.execute(  # type: ignore[assignment]
                sa.text(
                    f"SELECT COUNT(*) FROM {table} "
                    f"WHERE {col} IS NOT NULL AND {col} NOT IN ({quoted})"
                )
            ).scalar()
            results.append(CheckResult(
                dataset=table, check="validity", passed=(n == 0),
                detail=f"{col}: {n} value(s) outside {rule['allowed']}",
                value=n, threshold=0,
            ))
        elif rtype == "non_negative":
            n = conn.execute(  # type: ignore[assignment]
                sa.text(f"SELECT COUNT(*) FROM {table} WHERE {col} < 0")
            ).scalar()
            results.append(CheckResult(
                dataset=table, check="validity", passed=(n == 0),
                detail=f"{col}: {n} negative value(s)",
                value=n, threshold=0,
            ))
    return results


# ---------------------------------------------------------------------------
# Freshness
# ---------------------------------------------------------------------------

def check_freshness(
    conn: sa.engine.Connection,
    table: str,
    timestamp_col: str,
    max_age_seconds: int,
) -> CheckResult:
    """Latest row timestamp must be within max_age_seconds of now (UTC)."""
    raw = conn.execute(
        sa.text(f"SELECT MAX({timestamp_col}) FROM {table}")
    ).scalar()

    if raw is None:
        return CheckResult(
            dataset=table, check="freshness", passed=True,
            detail="empty table — no staleness to measure",
            value=None, threshold=max_age_seconds,
        )

    latest: datetime.datetime = (
        datetime.datetime.fromisoformat(raw) if isinstance(raw, str) else raw
    )
    # Normalise to naive UTC for comparison
    if latest.tzinfo is not None:
        latest = latest.utctimetuple()  # type: ignore[assignment]
        latest = datetime.datetime(*latest[:6])

    age = (datetime.datetime.utcnow() - latest).total_seconds()
    passed = age <= max_age_seconds
    return CheckResult(
        dataset=table, check="freshness", passed=passed,
        detail=f"{timestamp_col}: age={age:.0f}s (SLA≤{max_age_seconds}s)",
        value=round(age, 1),
        threshold=max_age_seconds,
    )
