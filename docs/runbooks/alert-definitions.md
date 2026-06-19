# Alert Definitions — Vecna Operations Console

This file is the single source of truth for alert conditions and their linked runbooks.  
Every alert entry maps a **signal** to a **severity**, a **runbook link**, and a **threshold**.

> **Rule:** No alert may be wired to monitoring without an entry here. Each entry links to a runbook with restart/backfill commands. Do not page on minor wobbles.

---

## A01 — API Server Liveness

| Field | Value |
|-------|-------|
| **Signal** | `GET /health` returns non-200 or connection refused |
| **Probe interval** | 30 s |
| **Threshold** | 3 consecutive failures (90 s) |
| **Severity** | SEV1 |
| **Runbook** | [sev1-runbook.md](sev1-runbook.md) → [tier0/api-server-rollback.md](tier0/api-server-rollback.md) |
| **Sample check** | `curl -sf http://127.0.0.1:8000/health` |

**Do not page on:** a single transient 502 from a load balancer restart.

---

## A02 — Deep Health Degradation

| Field | Value |
|-------|-------|
| **Signal** | `GET /api/health/deep` returns `database: false` or `model: ""` |
| **Probe interval** | 60 s |
| **Threshold** | 2 consecutive failures (2 min) |
| **Severity** | SEV1 (database false) / SEV2 (model empty) |
| **Runbook — database** | [sev1-runbook.md](sev1-runbook.md) → [tier0/database-rollback.md](tier0/database-rollback.md) |
| **Runbook — model** | [sev2-runbook.md](sev2-runbook.md) §3a |
| **Sample check** | `curl -s http://127.0.0.1:8000/api/health/deep \| jq '{db:.database,model:.model}'` |

---

## A03 — Operation Error Rate

| Field | Value |
|-------|-------|
| **Signal** | Proportion of `Operation.status == "error"` among operations started in the last 10 min |
| **Threshold (SEV2)** | >10 % error rate, sustained 10 min |
| **Threshold (SEV1)** | >80 % error rate, sustained 5 min |
| **Severity** | SEV2 → escalate to SEV1 |
| **Runbook** | [sev2-runbook.md](sev2-runbook.md) §3a → [sev1-runbook.md](sev1-runbook.md) |
| **Sample query** | `sqlite3 vecna.db "SELECT status, count(*) FROM operations WHERE created_at > datetime('now','-10 minutes') GROUP BY status;"` |

**Do not page on:** a single failed operation caused by an invalid user-supplied URL (these produce `error` status legitimately). Alert only on bulk failure.

---

## A04 — Operations Stuck in Running

| Field | Value |
|-------|-------|
| **Signal** | Count of operations with `status == "running"` AND `updated_at < now - 10 min` |
| **Threshold** | ≥ 3 such operations |
| **Severity** | SEV2 |
| **Runbook** | [sev2-runbook.md](sev2-runbook.md) §3a → [tier0/agent-runner-rollback.md](tier0/agent-runner-rollback.md) |
| **Sample query** | `sqlite3 vecna.db "SELECT count(*) FROM operations WHERE status='running' AND updated_at < datetime('now','-10 minutes');"` |

---

## A05 — Database Integrity

| Field | Value |
|-------|-------|
| **Signal** | `sqlite3 vecna.db "PRAGMA integrity_check;"` returns anything other than `ok` |
| **Probe interval** | 15 min |
| **Threshold** | Any single failure |
| **Severity** | SEV1 |
| **Runbook** | [sev1-runbook.md](sev1-runbook.md) → [tier0/database-rollback.md](tier0/database-rollback.md) |
| **Sample check** | `sqlite3 /path/to/vecna.db "PRAGMA integrity_check;"` |

---

## A06 — Metrics Endpoint Unavailable

| Field | Value |
|-------|-------|
| **Signal** | `GET /api/metrics` returns non-200 or times out |
| **Probe interval** | 60 s |
| **Threshold** | 5 consecutive failures (5 min) |
| **Severity** | SEV2 |
| **Runbook** | [sev2-runbook.md](sev2-runbook.md) → [tier0/api-server-rollback.md](tier0/api-server-rollback.md) |

---

## A07 — High Operation Latency

| Field | Value |
|-------|-------|
| **Signal** | Median time from operation creation to `done` status exceeds 3× rolling 7-day median |
| **Threshold** | Sustained >30 min |
| **Severity** | SEV2 |
| **Runbook** | [sev2-runbook.md](sev2-runbook.md) §3a (check LLM provider status first before rolling back) |

**Do not page on:** provider-wide slowness confirmed on the LLM provider's status page — this is not a Vecna Ops incident.

---

## Wiring alerts to this file

When configuring a monitoring tool (Prometheus Alertmanager, Grafana, PagerDuty, UptimeRobot, etc.) for each alert above:

1. Set the alert `name` to the `A0X` identifier (e.g. `vecna_A01_api_liveness`).
2. Add an annotation/label `runbook_url` pointing to the absolute GitHub URL of the linked runbook file.
3. Include the threshold and severity in the alert body so the on-call engineer sees them in the notification without needing to open this doc.

Example Prometheus alert annotations block:

```yaml
annotations:
  summary: "A01 — Vecna API server liveness failure"
  description: "GET /health has returned non-200 for 90 s. Immediate action required."
  runbook_url: "https://github.com/bdevierno1/Vecna-Operations-Console/blob/main/docs/runbooks/sev1-runbook.md"
  severity: "SEV1"
```
