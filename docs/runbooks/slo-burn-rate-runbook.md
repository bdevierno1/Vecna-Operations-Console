# SLO Burn-Rate Runbook

<!-- anchor: runbook-slo-burn-rate -->

Alert source: [`monitoring/alerts/slo-burn-rate.yml`](../../monitoring/alerts/slo-burn-rate.yml)
Policy: [`docs/slo-policy.md`](../slo-policy.md)

---

## Alert Reference

| Alert | Severity | Budget State | Burn Rate | Exhaustion Horizon |
|---|---|---|---|---|
| `VecnaAPIBurnRateRed` | critical | 🔴 Red | ≥ 14.4× | < 2 days |
| `VecnaAPIBurnRateYellow` | warning | 🟡 Yellow | ≥ 6× | ~5 days |
| `VecnaAPIBurnRateGreenElevated` | info | 🟢 Green (elevated) | ≥ 3× | ~9 days |
| `VecnaAgentBurnRateRed` | critical | 🔴 Red | ≥ 14.4× | < 2 days |
| `VecnaAgentBurnRateYellow` | warning | 🟡 Yellow | ≥ 6× | ~5 days |
| `VecnaAgentBurnRateGreenElevated` | info | 🟢 Green (elevated) | ≥ 3× | ~9 days |

---

## Step 1 — Acknowledge (within 5 min for Red/Yellow)

```
# Check current error rate from the metrics endpoint
curl -sf https://<HOST>/api/metrics | grep vecna_operations_total

# Check deep health (DB, model key, config)
curl -sf https://<HOST>/api/health/deep | jq .
```

---

## Step 2 — Identify Root Cause

### `vecna-api` alerts (operation success rate)

```bash
# Count terminal operations in the last hour by status
sqlite3 /opt/vecna-ops/backend/vecna_ops.db \
  "SELECT status, count(*) FROM operations
   WHERE created_at > datetime('now', '-1 hour')
   GROUP BY status;"

# Inspect recent failed operations for error patterns
sqlite3 /opt/vecna-ops/backend/vecna_ops.db \
  "SELECT id, error_message, created_at FROM operations
   WHERE status IN ('failed','error')
   ORDER BY created_at DESC LIMIT 20;"
```

Common causes:
- **LLM upstream error** — `error_message` contains rate-limit or auth text → check API key in `.env`, verify `LITELLM_MODEL_ID` prefix matches the key type.
- **Bad deploy** — check `git log -5` on host, compare deploy time to alert start time.
- **DB failure** — `"database"` key in `/api/health/deep` shows `"error"`.

### `vecna-agent` alerts

Same queries as above. Agent failures always surface as `status=failed|error` on `operations`.

---

## Step 3 — Respond by Severity

### 🔴 Red

1. **Freeze all deploys** immediately.
2. If a deploy landed in the last 60 min → **roll back** ([rollback runbook](production-rollback-runbook.md)).
3. Page on-call: PagerDuty `vecna-oncall`.
4. Confirm SLI recovers — watch `vecna_operations_total` for ~15 min.
5. Open postmortem if Red lasted > 20 min or budget < 10 % remaining.

### 🟡 Yellow

1. Freeze non-critical deploys. On-call reviews any pending release.
2. Open a ticket to track root cause; assign to current sprint.
3. Monitor every 30 min until burn rate drops below 6×.

### 🟢 Green Elevated

1. Create a sprint ticket: "SLO: slow elevated burn on `<service>`."
2. Investigate error patterns at next business-hours opportunity.
3. No immediate deploy freeze required.

---

## Step 4 — Verify Recovery

```bash
# SLI should recover within 15 min post-fix
curl -sf https://<HOST>/api/metrics | grep vecna_operations_total

# Confirm error rate back to baseline
# (completed / (completed + failed + error) ≥ 0.999 over last 30 min)
```

---

## Contacts

| Role | Where |
|---|---|
| On-call engineer | PagerDuty: `vecna-oncall` |
| Incident Commander | Declared at SEV start — see [sev1-response.md](sev1-response.md) |

---

*Last updated: 2026-06-18. Owner: on-call rotation.*
