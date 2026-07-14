"""Unit tests for the DORA delivery-metrics router (app/routers/dora.py)."""

from __future__ import annotations

import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app
from app.routers import dora as dora_mod
from app.routers.dora import (
    TARGETS,
    DoraMetrics,
    _compute,
    _git_log,
    _repo_root,
    get_dora_metrics,
)

UTC = timezone.utc


def _entry(
    author: datetime,
    committer: datetime,
    subject: str,
) -> tuple[datetime, datetime, str]:
    return (author, committer, subject)


# ---------------------------------------------------------------------------
# _compute
# ---------------------------------------------------------------------------
def test_compute_empty_returns_zeroed_metrics() -> None:
    m = _compute([], window_days=30)
    assert isinstance(m, DoraMetrics)
    assert m.deploy_frequency_per_day == 0.0
    assert m.lead_time_hours == 0.0
    assert m.change_failure_rate == 0.0
    assert m.recovery_time_hours == 0.0
    assert m.commit_count == 0
    assert m.deploy_count == 0
    assert m.fix_commit_count == 0
    assert m.window_days == 30
    assert m.targets == TARGETS


def test_compute_filters_trivial_commits_from_deploys() -> None:
    base = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    entries = [
        _entry(base, base, "feat: real feature"),
        _entry(base, base, "docs: update readme"),
        _entry(base, base, "chore(deps): bump lib"),
        _entry(base, base, "test: add coverage"),
        _entry(base, base, "refactor: meaningful change"),
    ]
    m = _compute(entries, window_days=10)
    # Only the two non-trivial commits count as deploys.
    assert m.deploy_count == 2
    assert m.commit_count == 5
    assert m.deploy_frequency_per_day == round(2 / 10, 4)


def test_compute_zero_window_days_yields_zero_frequency() -> None:
    base = datetime(2026, 1, 1, tzinfo=UTC)
    m = _compute([_entry(base, base, "feat: x")], window_days=0)
    assert m.deploy_frequency_per_day == 0.0


def test_compute_lead_time_is_median_and_skips_negative_deltas() -> None:
    a = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    entries = [
        # +2h lead time
        _entry(a, a + timedelta(hours=2), "feat: a"),
        # +4h lead time
        _entry(a, a + timedelta(hours=4), "feat: b"),
        # negative delta (committer before author) -> skipped
        _entry(a, a - timedelta(hours=1), "feat: c"),
    ]
    m = _compute(entries, window_days=7)
    # median of [2.0, 4.0] == 3.0
    assert m.lead_time_hours == 3.0


def test_compute_change_failure_rate_counts_fix_keywords() -> None:
    base = datetime(2026, 1, 1, tzinfo=UTC)
    entries = [
        _entry(base, base, "feat: new endpoint"),
        _entry(base, base, "fix: broken thing"),
        _entry(base, base, "hotfix urgent rollback"),
        _entry(base, base, "feat: another feature"),
    ]
    m = _compute(entries, window_days=30)
    # 2 of 4 meaningful commits are fixes.
    assert m.fix_commit_count == 2
    assert m.change_failure_rate == round(2 / 4, 4)


def test_compute_recovery_time_is_median_gap_between_fixes() -> None:
    a = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    entries = [
        _entry(a, a, "fix: one"),
        _entry(a, a + timedelta(hours=2), "fix: two"),
        _entry(a, a + timedelta(hours=6), "fix: three"),
    ]
    m = _compute(entries, window_days=30)
    # gaps: 2h and 4h -> median 3.0
    assert m.recovery_time_hours == 3.0


def test_compute_single_fix_has_zero_recovery_time() -> None:
    a = datetime(2026, 1, 1, tzinfo=UTC)
    m = _compute([_entry(a, a, "fix: only one")], window_days=30)
    assert m.recovery_time_hours == 0.0


# ---------------------------------------------------------------------------
# _repo_root
# ---------------------------------------------------------------------------
def test_repo_root_returns_dir_containing_git() -> None:
    root = _repo_root()
    assert isinstance(root, Path)
    # The resolved root must contain a .git entry (dir in a clone, file in a worktree).
    assert (root / ".git").exists()


# ---------------------------------------------------------------------------
# _git_log against a real throwaway repo
# ---------------------------------------------------------------------------
def _git(repo: Path, *args: str, when: str | None = None) -> None:
    env = {
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_SYSTEM": "/dev/null",
        "HOME": str(repo),
        "PATH": __import__("os").environ.get("PATH", ""),
    }
    if when is not None:
        env["GIT_AUTHOR_DATE"] = when
        env["GIT_COMMITTER_DATE"] = when
    subprocess.run(
        ["git", *args],
        cwd=str(repo),
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )


def test_git_log_reads_commits_from_main(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "checkout", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "Tester")

    (repo / "a.txt").write_text("1")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "feat: first", when="2026-01-01T00:00:00 +0000")

    (repo / "a.txt").write_text("2")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "fix: second", when="2026-01-02T00:00:00 +0000")

    entries = _git_log(repo, "2025-12-01T00:00:00+00:00")
    subjects = [s for _, _, s in entries]
    assert "feat: first" in subjects
    assert "fix: second" in subjects
    # Every record parses into (author_dt, committer_dt, subject).
    for a_dt, c_dt, subject in entries:
        assert isinstance(a_dt, datetime)
        assert isinstance(c_dt, datetime)
        assert isinstance(subject, str)


def test_git_log_returns_empty_on_non_git_dir(tmp_path: Path) -> None:
    # git log on a directory with no repo exits non-zero -> [].
    assert _git_log(tmp_path, "2025-01-01T00:00:00+00:00") == []


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------
def test_dora_endpoint_returns_full_schema() -> None:
    with TestClient(app) as c:
        r = c.get("/api/dora?window_days=14")
    assert r.status_code == 200
    body = r.json()
    for key in (
        "deploy_frequency_per_day",
        "lead_time_hours",
        "change_failure_rate",
        "recovery_time_hours",
        "window_days",
        "commit_count",
        "deploy_count",
        "fix_commit_count",
        "computed_at",
        "targets",
    ):
        assert key in body
    assert body["window_days"] == 14
    assert body["targets"] == TARGETS


def test_dora_endpoint_rejects_out_of_range_window() -> None:
    with TestClient(app) as c:
        too_small = c.get("/api/dora?window_days=0")
        too_large = c.get("/api/dora?window_days=999")
    assert too_small.status_code == 422
    assert too_large.status_code == 422


def test_get_dora_metrics_callable_directly() -> None:
    import asyncio

    result = asyncio.run(get_dora_metrics(window_days=7))
    assert isinstance(result, DoraMetrics)
    assert result.window_days == 7


def test_router_is_registered_on_app() -> None:
    with TestClient(app) as c:
        schema = c.get("/openapi.json").json()
    assert "/api/dora" in schema["paths"]
    assert dora_mod.router is not None
