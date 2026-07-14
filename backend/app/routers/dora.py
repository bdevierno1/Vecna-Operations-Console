"""DORA delivery metrics from git history (GET /api/dora).

Metrics over a rolling window on main: Deployment Frequency (non-trivial
commits/day), Lead Time (author→committer median, h), Change Failure Rate
(fix/revert %), Recovery Time (median inter-fix interval, h).
"""
from __future__ import annotations

import re
import statistics
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Query
from pydantic import BaseModel

router = APIRouter(tags=["dora"])

# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------
_TRIVIAL = re.compile(
    r"^(docs|style|test|chore|ci|build)(\([^)]+\))?!?:",
    re.IGNORECASE,
)
_FIX = re.compile(
    r"\b(fix|fixes|fixed|revert|hotfix|bugfix|patch|rollback|recover)\b",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Targets (from DORA spec)
# ---------------------------------------------------------------------------
TARGETS = {
    "deploy_frequency_per_day": 1.0,   # ≥ 1/day
    "lead_time_hours": 24.0,            # < 24 h
    "change_failure_rate": 0.15,        # < 15 %
    "recovery_time_hours": 1.0,         # < 1 h
}


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------
class DoraMetrics(BaseModel):
    deploy_frequency_per_day: float
    lead_time_hours: float
    change_failure_rate: float
    recovery_time_hours: float
    window_days: int
    commit_count: int
    deploy_count: int
    fix_commit_count: int
    computed_at: str
    targets: dict[str, float]


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------
def _repo_root() -> Path:
    """Walk up from this file to find the git root."""
    here = Path(__file__).resolve()
    for parent in [here, *here.parents]:
        if (parent / ".git").exists():
            return parent
    return here.parent


def _git_log(repo: Path, since_iso: str) -> list[tuple[datetime, datetime, str]]:
    """Return (author_dt, committer_dt, subject) for commits to main since *since_iso*.

    Format: <author_date>\x1f<commit_date>\x1f<subject>\x1e  (no leading separator)
    """
    # Field separator \x1f, record separator \x1e; no leading \x1f so split yields 3 fields.
    fmt = "%ai%x1f%ci%x1f%s%x1e"
    result = subprocess.run(
        ["git", "log", "main", f"--since={since_iso}", f"--format={fmt}"],
        cwd=str(repo),
        capture_output=True,
        text=True,
        timeout=15,
    )
    if result.returncode != 0:
        return []

    entries: list[tuple[datetime, datetime, str]] = []
    for record in result.stdout.split("\x1e"):
        record = record.strip()
        if not record:
            continue
        parts = record.split("\x1f")
        if len(parts) < 3:
            continue
        a_str, c_str, subject = parts[0], parts[1], parts[2]
        try:
            a_dt = datetime.fromisoformat(a_str.strip())
            c_dt = datetime.fromisoformat(c_str.strip())
        except ValueError:
            continue
        entries.append((a_dt, c_dt, subject.strip()))
    return entries


# ---------------------------------------------------------------------------
# Metric computation
# ---------------------------------------------------------------------------
def _compute(entries: list[tuple[datetime, datetime, str]], window_days: int) -> DoraMetrics:
    now = datetime.now(timezone.utc)

    meaningful = [(a, c, s) for a, c, s in entries if not _TRIVIAL.match(s)]
    deploys = meaningful  # every non-trivial commit to main = a deploy
    fixes = [(a, c, s) for a, c, s in meaningful if _FIX.search(s)]

    # Deployment frequency
    df = len(deploys) / window_days if window_days > 0 else 0.0

    # Lead time: median author→committer delta in hours
    lead_times = []
    for a, c, _ in meaningful:
        delta_h = (c - a).total_seconds() / 3600
        if delta_h >= 0:
            lead_times.append(delta_h)
    lt = statistics.median(lead_times) if lead_times else 0.0

    # Change failure rate
    cfr = len(fixes) / len(meaningful) if meaningful else 0.0

    # Recovery time: median gap between consecutive fix commits (hours)
    fix_times = sorted(c for _, c, _ in fixes)
    gaps = [
        (fix_times[i + 1] - fix_times[i]).total_seconds() / 3600
        for i in range(len(fix_times) - 1)
        if (fix_times[i + 1] - fix_times[i]).total_seconds() > 0
    ]
    rt = statistics.median(gaps) if gaps else 0.0

    return DoraMetrics(
        deploy_frequency_per_day=round(df, 4),
        lead_time_hours=round(lt, 2),
        change_failure_rate=round(cfr, 4),
        recovery_time_hours=round(rt, 2),
        window_days=window_days,
        commit_count=len(entries),
        deploy_count=len(deploys),
        fix_commit_count=len(fixes),
        computed_at=now.isoformat(),
        targets=TARGETS,
    )


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------
@router.get("/api/dora", response_model=DoraMetrics, summary="DORA delivery metrics")
async def get_dora_metrics(
    window_days: int = Query(default=30, ge=1, le=365, description="Rolling window in days"),
) -> DoraMetrics:
    """Compute DORA metrics from git history on the ``main`` branch."""
    repo = _repo_root()
    since = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    from datetime import timedelta
    since -= timedelta(days=window_days)
    entries = _git_log(repo, since.isoformat())
    return _compute(entries, window_days)
