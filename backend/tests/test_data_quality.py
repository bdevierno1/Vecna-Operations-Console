"""
Tests for data_quality.checks — run against an in-memory SQLite DB seeded
with known data so each dimension is verifiable without a live Vecna instance.
"""
from __future__ import annotations

import datetime

import pytest
import sqlalchemy as sa

from data_quality.checks import (
    CheckResult,
    check_completeness,
    check_freshness,
    check_uniqueness,
    check_validity,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def conn():
    """Sync in-memory SQLite connection with both core tables pre-created."""
    engine = sa.create_engine("sqlite:///:memory:", future=True)
    with engine.begin() as c:
        c.execute(sa.text("""
            CREATE TABLE operations (
                id          TEXT PRIMARY KEY,
                target_url  TEXT NOT NULL,
                status      TEXT NOT NULL DEFAULT 'pending',
                cost_usd    REAL NOT NULL DEFAULT 0.0,
                credits_used REAL NOT NULL DEFAULT 0.0,
                billable_total_usd REAL NOT NULL DEFAULT 0.0,
                created_at  TEXT NOT NULL,
                updated_at  TEXT NOT NULL
            )
        """))
        c.execute(sa.text("""
            CREATE TABLE billing_sessions (
                id                  TEXT PRIMARY KEY,
                total_cost_usd      REAL NOT NULL DEFAULT 0.0,
                total_credits       REAL NOT NULL DEFAULT 0.0,
                total_input_tokens  INTEGER NOT NULL DEFAULT 0,
                total_output_tokens INTEGER NOT NULL DEFAULT 0,
                total_billable_usd  REAL NOT NULL DEFAULT 0.0,
                created_at          TEXT NOT NULL,
                updated_at          TEXT NOT NULL
            )
        """))
    with engine.connect() as c:
        yield c


def _now() -> str:
    return datetime.datetime.utcnow().isoformat()


def _op(conn, id_="op-1", status="completed", cost=0.5, credits=1.0,
         billable=0.5, target_url="https://example.com", updated_at=None):
    conn.execute(sa.text("""
        INSERT INTO operations (id, target_url, status, cost_usd, credits_used,
                                billable_total_usd, created_at, updated_at)
        VALUES (:id, :url, :status, :cost, :credits, :billable, :now, :upd)
    """), {
        "id": id_, "url": target_url, "status": status, "cost": cost,
        "credits": credits, "billable": billable,
        "now": _now(), "upd": updated_at or _now(),
    })
    conn.commit()


def _session(conn, id_="sess-1", cost=0.0, credits=0.0, billable=0.0,
              updated_at=None):
    conn.execute(sa.text("""
        INSERT INTO billing_sessions
            (id, total_cost_usd, total_credits, total_input_tokens,
             total_output_tokens, total_billable_usd, created_at, updated_at)
        VALUES (:id, :cost, :credits, 0, 0, :billable, :now, :upd)
    """), {
        "id": id_, "cost": cost, "credits": credits, "billable": billable,
        "now": _now(), "upd": updated_at or _now(),
    })
    conn.commit()


# ---------------------------------------------------------------------------
# Completeness
# ---------------------------------------------------------------------------

class TestCompleteness:
    def test_passes_on_full_data(self, conn):
        _op(conn)
        results = check_completeness(conn, "operations", ["id", "target_url", "status"])
        assert all(r.passed for r in results)

    def test_empty_table_passes(self, conn):
        results = check_completeness(conn, "operations", ["id", "target_url"])
        assert all(r.passed for r in results)
        assert all("empty table" in r.detail for r in results)

    def test_fails_when_null_rate_exceeds_threshold(self, conn):
        # Simulate a column that carries nulls in an analytics replica — use a
        # separate table without NOT NULL on the tested column.
        conn.execute(sa.text("""
            CREATE TABLE replica_ops (id TEXT PRIMARY KEY, url TEXT, ts TEXT)
        """))
        conn.execute(sa.text("INSERT INTO replica_ops VALUES ('r1', NULL, :now)"), {"now": _now()})
        conn.commit()
        results = check_completeness(conn, "replica_ops", ["url"])
        assert len(results) == 1
        assert not results[0].passed
        assert results[0].value == 100.0


# ---------------------------------------------------------------------------
# Uniqueness
# ---------------------------------------------------------------------------

class TestUniqueness:
    def test_passes_unique_pk(self, conn):
        _op(conn, id_="op-a")
        _op(conn, id_="op-b")
        r = check_uniqueness(conn, "operations", "id")
        assert r.passed
        assert r.value == 0

    def test_empty_table_passes(self, conn):
        r = check_uniqueness(conn, "operations", "id")
        assert r.passed

    def test_fails_on_duplicate_pk(self, conn):
        # Force duplicate via INSERT OR IGNORE bypass: use a raw insert trick.
        # SQLite enforces PK uniqueness, so we'll create a temp table to simulate.
        conn.execute(sa.text("""
            CREATE TABLE ops_dup (id TEXT, val TEXT)
        """))
        conn.execute(sa.text("INSERT INTO ops_dup VALUES ('x', 'a')"))
        conn.execute(sa.text("INSERT INTO ops_dup VALUES ('x', 'b')"))
        conn.commit()
        r = check_uniqueness(conn, "ops_dup", "id")
        assert not r.passed
        assert r.value == 1


# ---------------------------------------------------------------------------
# Validity
# ---------------------------------------------------------------------------

class TestValidity:
    def test_valid_status_passes(self, conn):
        _op(conn, status="completed")
        results = check_validity(conn, "operations", [
            {"type": "allowed_values", "col": "status",
             "allowed": ["pending", "running", "completed", "failed"]},
        ])
        assert all(r.passed for r in results)

    def test_invalid_status_fails(self, conn):
        _op(conn, status="unknown_status")
        results = check_validity(conn, "operations", [
            {"type": "allowed_values", "col": "status",
             "allowed": ["pending", "running", "completed", "failed"]},
        ])
        assert not results[0].passed
        assert results[0].value == 1

    def test_non_negative_passes(self, conn):
        _op(conn, cost=0.0, credits=5.0)
        results = check_validity(conn, "operations", [
            {"type": "non_negative", "col": "cost_usd"},
            {"type": "non_negative", "col": "credits_used"},
        ])
        assert all(r.passed for r in results)

    def test_negative_cost_fails(self, conn):
        _op(conn, cost=-1.0)
        results = check_validity(conn, "operations", [
            {"type": "non_negative", "col": "cost_usd"},
        ])
        assert not results[0].passed
        assert results[0].value == 1


# ---------------------------------------------------------------------------
# Freshness
# ---------------------------------------------------------------------------

class TestFreshness:
    def test_recent_row_passes(self, conn):
        _op(conn)   # updated_at = now
        r = check_freshness(conn, "operations", "updated_at", max_age_seconds=3600)
        assert r.passed

    def test_empty_table_passes(self, conn):
        r = check_freshness(conn, "operations", "updated_at", max_age_seconds=60)
        assert r.passed
        assert "empty table" in r.detail

    def test_stale_row_fails(self, conn):
        two_hours_ago = (
            datetime.datetime.utcnow() - datetime.timedelta(hours=2)
        ).isoformat()
        _op(conn, updated_at=two_hours_ago)
        r = check_freshness(conn, "operations", "updated_at", max_age_seconds=3600)
        assert not r.passed
        assert r.value > 3600


# ---------------------------------------------------------------------------
# Contract smoke-test: contracts load without error
# ---------------------------------------------------------------------------

def test_contracts_load():
    from data_quality.runner import load_contracts
    contracts = load_contracts()
    assert len(contracts) == 2
    tables = {c["dataset"]["table"] for c in contracts}
    assert "operations" in tables
    assert "billing_sessions" in tables
    for c in contracts:
        assert "quality_rules" in c
        assert "slas" in c
        assert c["slas"]["freshness_max_seconds"] > 0
