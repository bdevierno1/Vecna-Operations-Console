# SEV1 Incident Runbook — Vecna Operations Console

**Severity:** SEV1 — Complete service outage or critical data loss  
**Response SLA:** Acknowledge within 5 min · Mitigate within 30 min · Resolve within 2 h  
**Postmortem SLA:** Blameless postmortem within 3 business days  
**Template:** [docs/postmortem-template.md](../postmortem-template.md)

---

## SEV1 definition

A SEV1 exists when **any** of the following is true:

- All operations fail to start (API returns 5xx on `POST /api/operations`)
- `GET /health` returns non-200 or is unreachable
- Database is corrupted or unreadable
- All in-flight operations are silently stuck in `running` with no events
- Data loss confirmed (operations deleted or findings lost outside normal flow)
- Security breach — unauthorized access to operations data or the SQLite file

---

## Step 1 — Acknowledge (< 5 min)

1. Claim the incident in the alert channel: "@here I'm IC on this."
2. Open an incident channel: `#inc-YYYYMMDD-<short-description>`.
3. Post initial status: "SEV1 declared. Service: Vecna Ops Console. Symptom: `<one line>`. IC: `<name>`."
4. Page backup if this is outside business hours.

---

## Step 2 — Assess (< 10 min from acknowledge)

Run these checks in order. Stop at the first failure — that is your blast radius.

```bash
HOST=http://127.0.0.1:8000   # adjust to your deployed hostname

# Is the process alive?
pgrep -a uvicorn

# Basic liveness
curl -s -o /dev/null -w "%{http_code}" $HOST/health

# Deep health: DB + model
curl -s $HOST/api/health/deep | jq .

# Recent errors in logs
journalctl -u vecna-api -n 100 --no-pager | grep -E "ERROR|CRITICAL|Traceback" | tail -20
# or: tail -100 /path/to/uvicorn.log | grep -E "ERROR|CRITICAL"

# Database integrity
sqlite3 /path/to/vecna.db "PRAGMA integrity_check;" 2>/dev/null || echo "DB UNREACHABLE"

# Active operation count
curl -s $HOST/api/operations | jq '[.[] | select(.status == "running")] | length'
```

Record findings in the incident channel before acting.

---

## Step 3 — Contain

Apply the appropriate Tier-0 rollback runbook based on your assessment:

| Component failing | Runbook |
|-------------------|---------|
| API server (FastAPI/Uvicorn process) | [tier0/api-server-rollback.md](tier0/api-server-rollback.md) |
| Agent runner (operations stuck/erroring) | [tier0/agent-runner-rollback.md](tier0/agent-runner-rollback.md) |
| Database (integrity check failure, schema error) | [tier0/database-rollback.md](tier0/database-rollback.md) |
| Frontend (UI blank, JS crash) | [tier0/frontend-rollback.md](tier0/frontend-rollback.md) |

If multiple components are failing, address in this order: **Database → API Server → Agent Runner → Frontend**.

---

## Step 4 — Communicate (every 15 min until resolved)

Post in `#inc-YYYYMMDD-<short-description>`:

```
[HH:MM UTC] Status update
- Symptom: <what is failing>
- Blast radius: <who is affected, how many operations impacted>
- Action taken: <what you did in last 15 min>
- Next action: <what you are doing now>
- ETA to resolution: <estimate or "unknown">
```

---

## Step 5 — Resolve

Resolution criteria — **all** must be true:

- [ ] `GET /health` returns 200
- [ ] `GET /api/health/deep` shows `database: true` and `model: <non-empty>`
- [ ] A smoke-test operation on `https://example.com` reaches `done` status
- [ ] No `ERROR` or `CRITICAL` entries in logs for 10 consecutive minutes

Post in incident channel: "SEV1 resolved at [HH:MM UTC]. All checks pass."

---

## Step 6 — Learn

- Open a postmortem issue within 3 business days.
- Use template: [docs/postmortem-template.md](../postmortem-template.md).
- Schedule a 30-min review meeting with the team.
- Update this runbook if any step was missing or wrong.

---

## Quick reference

| Resource | Location |
|----------|----------|
| Health endpoint | `GET /health` |
| Deep health | `GET /api/health/deep` |
| Metrics | `GET /api/metrics` |
| API server rollback | [tier0/api-server-rollback.md](tier0/api-server-rollback.md) |
| Agent runner rollback | [tier0/agent-runner-rollback.md](tier0/agent-runner-rollback.md) |
| Database rollback | [tier0/database-rollback.md](tier0/database-rollback.md) |
| Frontend rollback | [tier0/frontend-rollback.md](tier0/frontend-rollback.md) |
| Postmortem template | [../postmortem-template.md](../postmortem-template.md) |
