# SLO & Error-Budget Policy — Vecna Operations Console

<!-- anchor: slo-policy -->

Follows the org-wide [SLO & Error-Budget Policy](https://vecna.ai/docs/slo-policy). Alert
rules live in [`monitoring/alerts/slo-burn-rate.yml`](../monitoring/alerts/slo-burn-rate.yml).
Runbook: [`docs/runbooks/slo-burn-rate-runbook.md`](runbooks/slo-burn-rate-runbook.md).

---

## 1. Service Tier Classification

> Tier 0 = payments, auth (not present in this repo).
> The rollback runbook's "Tier-0 Services" section uses local terminology for
> "essential to this app" — the org-wide tier for all three services below is **Tier 1**.

| Service | Tier | Process | Why Tier 1 |
|---|---|---|---|
| `vecna-api` | **1** | `uvicorn app.main:app` | Core REST + WebSocket API; all client traffic |
| `vecna-agent` | **1** | asyncio tasks inside `vecna-api` | Primary product function; failures = user-visible errors |
| `vecna-frontend` | **1** | Vite static bundle / CDN | UI surface; outage = zero usability |
| `vecna-desktop` | 2 | Electron process | Optional desktop wrapper; degraded ≠ product down |

---

## 2. SLI Definitions

### 2.1 `vecna-api` — Operation Success Rate

**What:** fraction of terminal-state operations that completed successfully.

```
SLI = rate(vecna_operations_total{status="completed"}[window])
      / rate(vecna_operations_total{status!~"pending|running"}[window])
```

Data source: `GET /api/metrics` (exposed by `backend/app/main.py`).

**Latency SLI (future):** p99 < 500 ms on `POST /api/operations`. Requires adding
`vecna_operation_duration_seconds` histogram to `app/services/runner.py`. Tracked in
follow-up work.

### 2.2 `vecna-api` — Availability (HTTP)

**What:** fraction of seconds `GET /health` returns HTTP 200.

Data source: Blackbox Exporter probe against `<HOST>/health`.

### 2.3 `vecna-agent` — Operation Success Rate

**What:** fraction of started operations that reach `status=completed`.

Same metric source as 2.1; SLO target is lower to account for expected upstream LLM failures.

### 2.4 `vecna-frontend` — Availability

**What:** fraction of seconds `GET <FRONTEND_ORIGIN>/` returns HTTP 200.

Data source: Blackbox Exporter probe.

---

## 3. SLO Targets and Error Budgets

All windows are rolling 28-day.

| Service | SLI | Target | Error Budget |
|---|---|---|---|
| `vecna-api` | Operation success rate | **99.9 %** | 43.2 min / 28 d |
| `vecna-api` | HTTP availability (`/health`) | **99.9 %** | 43.2 min / 28 d |
| `vecna-agent` | Operation success rate | **99.0 %** | 6.7 h / 28 d |
| `vecna-frontend` | HTTP availability | **99.9 %** | 43.2 min / 28 d |
| `vecna-desktop` | Process availability | **99.5 %** | 3.6 h / 28 d |

---

## 4. Error-Budget States and Burn-Rate Alert Thresholds

| State | Budget Remaining | `vecna-api` (99.9%) | `vecna-agent` (99.0%) | Response |
|---|---|---|---|---|
| 🟢 Green | > 75 % | Slow elevated burn: ≥ 3× over 3 d | ≥ 3× over 3 d | Ticket; fix in sprint |
| 🟡 Yellow | 25 – 75 % | Medium burn: ≥ 6× over 6 h | ≥ 6× over 6 h | Page during business hours |
| 🔴 Red | < 25 % | Fast burn: ≥ 14.4× over 1 h | ≥ 14.4× over 1 h | Page immediately |

Burn rate = observed error rate ÷ SLO error rate (1 − target).

---

## 5. Operational Constraints by State

| State | Constraint |
|---|---|
| 🟢 Green | Normal deployments permitted. |
| 🟡 Yellow | Freeze non-critical deploys. On-call reviews each release before shipping. |
| 🔴 Red | **All deploys frozen** until budget recovers to Yellow. Rollback is default action. |

See [`docs/runbooks/production-rollback-runbook.md`](runbooks/production-rollback-runbook.md)
for rollback procedures.

---

## 6. SLO Review Triggers

- Budget hits Red for any Tier-1 service → postmortem required within 48 h.
- Budget hits Red twice in one 28-day window → formal SLO target reassessment.
- New service in critical path → must be tiered and have SLOs before first production deploy.

---

*Last updated: 2026-06-18. Owner: on-call rotation.*
