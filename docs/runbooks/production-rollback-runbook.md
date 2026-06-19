# Production Rollback Runbook

<!-- anchor: runbook-rollback -->

**Golden rule: restore the user first; debug second.** No production change ships without a tested rollback path. When in doubt, roll back before you diagnose.

---

## Decision: roll back vs. roll forward

| Condition | Action |
|---|---|
| Bad deploy landed in the last 60 min | **Roll back** (this doc) |
| Impact growing over time | **Roll back** |
| Trivial, well-understood one-line fix, rollback slower than fix | Roll forward (second pair of eyes required) |
| DB migration is the culprit and is not reversible | **Page DB owner and on-call DBA — do NOT auto-rollback schema** |

---

## Tier-0 Services

| Service | Process | What breaks when it's down |
|---|---|---|
| `vecna-api` | `uvicorn app.main:app` | All HTTP + WebSocket traffic — 100% customer impact |
| `vecna-runner` | Async tasks inside `vecna-api` (see `services/runner.py`) | New operations hang; existing WS streams stop |
| `vecna-db` | SQLite file (`vecna_ops.db` by default) | All reads/writes fail; API returns 500 on any data path |

---

## Rollback Procedures

### Service 1 — `vecna-api` (FastAPI / uvicorn)

**Symptoms:** `/health` returns non-200, new operations return 500, WebSocket connects then immediately closes.

#### Process-managed deployment (systemd / supervisor)

```bash
# 1. Identify the bad commit
git -C /opt/vecna-ops log --oneline -10

# 2. Pin to the last-known-good SHA (replace <GOOD_SHA>)
GOOD_SHA=<GOOD_SHA>
git -C /opt/vecna-ops checkout "$GOOD_SHA"

# 3. Reinstall dependencies if requirements.txt changed
cd /opt/vecna-ops/backend
pip install -r requirements.txt --quiet

# 4. Restart the service
sudo systemctl restart vecna-api   # or: supervisorctl restart vecna-api

# 5. Verify (wait up to 30 s)
until curl -sf http://localhost:8000/health | grep -q '"status"'; do sleep 2; done
echo "API healthy"
```

#### Kubernetes deployment

```bash
# Inspect revision history
kubectl rollout history deploy/vecna-api -n vecna

# Roll back to the previous revision
kubectl rollout undo deploy/vecna-api -n vecna

# Verify rollout completes
kubectl rollout status deploy/vecna-api -n vecna
# Expected output: "successfully rolled out"

# Confirm pods are Running + Ready
kubectl get pods -n vecna -l app=vecna-api
```

**Post-rollback health check:**
```bash
curl -sf https://<YOUR_HOST>/api/health/deep | jq .
# All keys must be green; "database" must be "ok"
```

---

### Service 2 — `vecna-runner` (agent executor)

The runner is an `asyncio` task spawned inside the `vecna-api` process. Rolling back `vecna-api` also rolls back the runner. No separate step required.

**If individual operations are stuck (not the whole API):**

```bash
# Check for stuck operations in DB
sqlite3 /opt/vecna-ops/backend/vecna_ops.db \
  "SELECT id, status, created_at FROM operations WHERE status='running' ORDER BY created_at DESC LIMIT 10;"

# Force-fail a stuck operation (replace <OP_ID>)
sqlite3 /opt/vecna-ops/backend/vecna_ops.db \
  "UPDATE operations SET status='error', updated_at=datetime('now') WHERE id='<OP_ID>' AND status='running';"

# Restart API to flush in-flight asyncio tasks
sudo systemctl restart vecna-api
```

**Kubernetes:**
```bash
# Restart pods to flush in-flight tasks (rolling restart, zero downtime)
kubectl rollout restart deploy/vecna-api -n vecna
kubectl rollout status deploy/vecna-api -n vecna
```

---

### Service 3 — `vecna-db` (SQLite)

#### Schema migration rollback

Migrations in `backend/app/schema_migrate.py` are **additive only** (ADD COLUMN). SQLite does not support DROP COLUMN in older versions.

**If a new column caused unexpected behaviour:**

```bash
# Safe: new columns have DEFAULT values; old code ignores unknown columns.
# Roll back the application code (step above) — the extra column is inert.
# Do NOT attempt to drop the column unless you have confirmed SQLite ≥ 3.35.0
sqlite3 --version
```

**If a column must be removed (non-reversible migration):**

> ⚠️ **Stop. Do not auto-rollback the schema.**
> Page the DB owner and the on-call DBA immediately.
> The downstream table retains yesterday's state until the DBA clears it.

```bash
# Page DB owner
# Page on-call DBA
# Document the stuck migration in the incident channel
# Do not run DROP COLUMN without explicit DBA sign-off
```

#### Database file recovery

```bash
# Verify integrity before any recovery action
sqlite3 /opt/vecna-ops/backend/vecna_ops.db "PRAGMA integrity_check;"
# Expected: "ok"

# Restore from most recent backup (adjust path)
BACKUP_DIR=/var/backups/vecna-db
LATEST=$(ls -t "$BACKUP_DIR"/vecna_ops_*.db | head -1)
echo "Restoring from $LATEST"
cp /opt/vecna-ops/backend/vecna_ops.db \
   /opt/vecna-ops/backend/vecna_ops.db.pre-restore-$(date +%s)
cp "$LATEST" /opt/vecna-ops/backend/vecna_ops.db
sudo systemctl restart vecna-api
```

---

## Post-Rollback Verification Checklist

Run these in order; do not close the incident until all five pass:

- [ ] `GET /api/health/deep` returns HTTP 200 with `"database": "ok"` and no warnings
- [ ] Error rate (4xx/5xx) at or below baseline for ≥ 15 min
- [ ] p99 latency at or below SLO for ≥ 15 min
- [ ] Any feature flag that was added by the bad deploy is disabled or removed
- [ ] Incident channel marked resolved; postmortem opened (SEV1/SEV2) — see [postmortem-template.md](postmortem-template.md)

---

## Contacts

| Role | Where to page |
|---|---|
| On-call engineer | PagerDuty: `vecna-oncall` |
| DB owner | PagerDuty: `vecna-db-owner` |
| On-call DBA | PagerDuty: `vecna-dba-oncall` |
| Incident Commander | Declared at SEV start; see [sev1-response.md](sev1-response.md) |

---

*Last updated: 2026-06-18. Owner: on-call rotation.*
