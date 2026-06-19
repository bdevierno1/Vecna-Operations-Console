"""
CLI runner — loads contracts from YAML and runs all checks against the live DB.

Usage:
    python -m data_quality.runner [--db-url sqlite:///./vecna.db]

Exit codes:
    0  all checks passed
    1  one or more checks failed
    2  usage / configuration error
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import sqlalchemy as sa
import yaml

from data_quality.checks import (
    CheckResult,
    check_completeness,
    check_freshness,
    check_uniqueness,
    check_validity,
)

CONTRACTS_DIR = Path(__file__).parent / "contracts"


def load_contracts() -> list[dict]:
    contracts = []
    for path in sorted(CONTRACTS_DIR.glob("*.yaml")):
        with path.open() as fh:
            contracts.append(yaml.safe_load(fh))
    return contracts


def run_contract(conn: sa.engine.Connection, contract: dict) -> list[CheckResult]:
    table = contract["dataset"]["table"]
    quality = contract.get("quality_rules", {})
    sla = contract.get("slas", {})
    results: list[CheckResult] = []

    # Completeness
    not_null_cols = quality.get("not_null_cols", [])
    if not_null_cols:
        results.extend(
            check_completeness(
                conn, table, not_null_cols,
                threshold_pct=quality.get("completeness_threshold_pct", 0.5),
            )
        )

    # Uniqueness
    pk = quality.get("primary_key")
    if pk:
        results.append(check_uniqueness(conn, table, pk))

    # Validity
    validity_rules = quality.get("validity_rules", [])
    if validity_rules:
        results.extend(check_validity(conn, table, validity_rules))

    # Freshness
    ts_col = sla.get("freshness_timestamp_col")
    max_age = sla.get("freshness_max_seconds")
    if ts_col and max_age is not None:
        results.append(check_freshness(conn, table, ts_col, int(max_age)))

    return results


def main(db_url: str | None = None) -> int:
    url = db_url or os.environ.get("DATABASE_URL", "sqlite:///./vecna.db")
    # Strip async driver prefix for sync runner
    url = url.replace("+aiosqlite", "")

    engine = sa.create_engine(url)
    contracts = load_contracts()
    if not contracts:
        print("ERROR: no contracts found in", CONTRACTS_DIR, file=sys.stderr)
        return 2

    all_results: list[CheckResult] = []
    with engine.connect() as conn:
        for contract in contracts:
            all_results.extend(run_contract(conn, contract))

    failed = [r for r in all_results if not r.passed]
    for r in all_results:
        print(r)

    if failed:
        print(f"\n{len(failed)}/{len(all_results)} check(s) FAILED", file=sys.stderr)
        return 1

    print(f"\n{len(all_results)}/{len(all_results)} check(s) passed")
    return 0


if __name__ == "__main__":
    db_url_arg = sys.argv[1] if len(sys.argv) > 1 else None
    sys.exit(main(db_url_arg))
