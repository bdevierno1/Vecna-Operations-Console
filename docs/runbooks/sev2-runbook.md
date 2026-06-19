# SEV2 Incident Runbook — Vecna Operations Console

**Severity:** SEV2 — Significant degradation; service partially available  
**Response SLA:** Acknowledge within 15 min · Mitigate within 2 h · Resolve within 8 h  
**Postmortem SLA:** Blameless postmortem within 3 business days  
**Template:** [docs/postmortem-template.md](../postmortem-template.md)

---

## SEV2 definition

A SEV2 exists when **any** of the following is true and the service is partially available:

- Operation error rate >10 % but <100 % (some operations completing)
- WebSocket streaming broken for a subset of users
- LLM authentication failures causing intermittent operation failures
- Rate limiting incorrectly blocking legitimate requests
- Frontend degraded (some panels not rendering, findings not loading) but page loads
- Billing session data incorrect or missing for recent operations
- Schema migration partially applied causing some columns to be missing
- Elevated latency (operations taking >3× normal time to complete)

> If the issue escalates to complete outage, re-declare as [SEV1](sev1-runbook.md).

---

## Step 1 — Acknowledge (< 15 min)

1. Respond in the alert channel: "I'm looking at this — <name>."
2. Open an incident thread or channel: `#inc-YYYYMMDD-<description>`.
3. Post initial triage: "SEV2 declared. Symptom: `<one line>`. IC: `<name>`. Investigating."

---

## Step 2 — Assess (< 30 min from acknowledge)

```bash
HOST=http://127.0.0.1:8000   # adjust to your deployed hostname

# Service liveness
curl -s -o /dev/null -w "%{http_code}" $HOST/health

# Deep health
curl -s $HOST/api/health/deep | jq .

# Recent error log sample
journalctl -u vecna-api -n 200 --no-pager | grep -E "ERROR|WARNING|Traceback" | tail -30
# or: tail -200 /path/to/uvicorn.log | grep -E "ERROR|WARNING"

# Operation status breakdown
curl -s $HOST/api/operations | jq 'group_by(.status) | map({status: .[0].status, count: length})'

# Rate limit state (if rate limiting is suspected)
curl -s $HOST/api/health/deep | jq '.rate_limits'

# LLM connectivity test (via health deep)
curl -s $HOST/api/health/deep | jq '{model_id: .model_id, model_ok: .model}'

# Check recent schema migration log
grep -i "migration\|migrate\|schema" /path/to/uvicorn.log | tail -20
```

---

## Step 3 — Diagnose and contain

### 3a. Elevated error rate on operations

```bash
# Find the failing operations and get their error messages
curl -s $HOST/api/operations | jq '[.[] | select(.status == "error") | {id:.id, created_at:.created_at}]' | head -10

# Check events_json on a failing operation for the error frame
OP_ID="<op-id-from-above>"
curl -s $HOST/api/operations/$OP_ID | jq '.events_json | fromjson | .[] | select(.type == "error")'
```

If the error is a **LiteLLM auth failure** → check `.env` for key/model mismatch; fix config and restart.  
If the error is a **tool timeout** → check network egress and `httpx` timeout settings in `http_client.py`.  
If the error is a **runner crash** → roll back to previous commit via [tier0/agent-runner-rollback.md](tier0/agent-runner-rollback.md).

### 3b. WebSocket streaming broken

```bash
# Confirm WebSocket route is registered
curl -s $HOST/api/health/deep | jq .

# Test WebSocket manually (requires wscat)
wscat -c ws://127.0.0.1:8000/ws/<op-id>
```

If WebSocket is broken after a code change → roll back via [tier0/api-server-rollback.md](tier0/api-server-rollback.md).

### 3c. Rate limiting blocking legitimate requests

```bash
# Check current limits configuration
curl -s $HOST/api/health/deep | jq '.rate_limits'
```

If limits are misconfigured → update `RATE_LIMIT_*` env vars in `.env` and restart the server.  
If a per-IP limit is legitimately triggering → no rollback needed; document as expected behavior.

### 3d. Schema migration partially applied

```bash
sqlite3 /path/to/vecna.db "PRAGMA table_info(operations);"
sqlite3 /path/to/vecna.db "PRAGMA table_info(billing_sessions);"
```

If columns are missing → follow [tier0/database-rollback.md](tier0/database-rollback.md) §4b (schema-only rollback).

### 3e. Billing session data incorrect

```bash
# Inspect a specific billing session
SESSION_ID="<session-id>"
curl -s $HOST/api/billing/sessions/$SESSION_ID | jq .
```

Billing data is computed client-side with `localStorage` and aggregated server-side. Incorrect totals are usually a display bug, not data loss. Roll back the frontend if a recent frontend change is suspected: [tier0/frontend-rollback.md](tier0/frontend-rollback.md).

---

## Step 4 — Communicate (every 30 min until resolved)

Post in incident thread:

```
[HH:MM UTC] SEV2 update
- Symptom: <what is degraded>
- Blast radius: <% of users/operations affected>
- Root cause hypothesis: <current best guess>
- Actions taken: <last 30 min>
- Next step: <immediate action>
- ETA: <estimate or "investigating">
```

---

## Step 5 — Resolve

Resolution criteria — **all** must be true:

- [ ] `GET /health` returns 200
- [ ] `GET /api/health/deep` shows `database: true`
- [ ] Operation error rate back to baseline (<2 %) for 15 consecutive minutes
- [ ] WebSocket events stream correctly for a test operation
- [ ] No anomalous `ERROR` log entries for 15 consecutive minutes

Post in incident channel: "SEV2 resolved at [HH:MM UTC]. Monitoring for 30 min."

---

## Step 6 — Learn

- Open a postmortem issue within 3 business days.
- Use template: [docs/postmortem-template.md](../postmortem-template.md).
- Update contributing runbooks if any steps were missing or incorrect.

---

## Quick reference

| Resource | Location |
|----------|----------|
| Health endpoint | `GET /health` |
| Deep health | `GET /api/health/deep` |
| API server rollback | [tier0/api-server-rollback.md](tier0/api-server-rollback.md) |
| Agent runner rollback | [tier0/agent-runner-rollback.md](tier0/agent-runner-rollback.md) |
| Database rollback | [tier0/database-rollback.md](tier0/database-rollback.md) |
| Frontend rollback | [tier0/frontend-rollback.md](tier0/frontend-rollback.md) |
| Escalate to SEV1 | [sev1-runbook.md](sev1-runbook.md) |
| Postmortem template | [../postmortem-template.md](../postmortem-template.md) |
