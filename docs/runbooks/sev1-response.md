# SEV1 Incident Response Runbook

<!-- anchor: runbook-sev1 -->

> **Alert annotation URL:** `docs/runbooks/sev1-response.md#runbook-sev1`
> Embed this anchor in PagerDuty / Alertmanager annotations so on-call lands here directly.

---

## Definition

**SEV1 = customer-wide outage or data loss.**

Triggering conditions (any one is sufficient):
- `vecna-api` health check fails for > 2 consecutive minutes
- Error rate across all endpoints > 25% sustained for > 5 min
- Database unreachable (all `/api/*` routes return 500)
- Any confirmed data loss or data corruption in `vecna_ops.db`
- p99 latency > 10× SLO for > 5 min with no recovery trend

---

## Immediate Actions (first 5 minutes)

SLA: acknowledge the alert within **5 minutes** of detection.

```
[ ] Acknowledge alert in PagerDuty
[ ] Join #incidents Slack channel (or open one if absent)
[ ] Declare severity: post "SEV1 DECLARED — <brief symptom>" in channel
[ ] Assign Incident Commander (IC) — IC owns comms, not hands-on work
[ ] Assign Ops Lead — separate from IC; drives mitigation
```

**First technical check — in this order:**

1. **Recent deploy?** → Roll back immediately; do not debug in-place.
   ```bash
   # See production-rollback-runbook.md#vecna-api for full steps
   git -C /opt/vecna-ops log --oneline -5
   # If the top commit is less than ~1h old and correlates with the incident start → rollback
   sudo systemctl restart vecna-api   # after git checkout <GOOD_SHA>
   ```

2. **Single-region / AZ failure?** → Failover to standby region or secondary instance.

3. **Load spike?** → Enable rate limiting, scale horizontally, or shed non-essential traffic.

> **Rule: stabilize before you diagnose.** Rollback first; root-cause analysis happens after users are restored.

---

## IC Responsibilities During a SEV1

The **Incident Commander** owns:
- Opening and maintaining the incident channel
- Assigning roles (Ops Lead, Comms, Scribe)
- Executive communication every **30 minutes** until resolved
- Declaring the incident closed once SLIs recover

The IC does **not** do hands-on technical work while commanding.

**Executive update template (every 30 min):**
```
SEV1 UPDATE [HH:MM UTC]
Status: <investigating | mitigating | resolved>
Impact: <what is broken / how many users affected>
Last action: <what was just done>
Next action: <what is happening next>
ETA: <best estimate or "unknown">
```

---

## Escalation

| Time since declare | Action |
|---|---|
| 0 min | Page on-call engineer |
| 10 min | Page engineering lead if no active mitigation in progress |
| 20 min | Page VP Engineering; notify customer success |
| 30 min | First executive update |
| 60 min | Re-evaluate if rollback is not sufficient; consider DR |

---

## Recovery Criteria

Do not declare recovery until **all** of the following hold for ≥ 15 min:

- `GET /api/health/deep` returns 200, `"database": "ok"`
- Error rate ≤ baseline
- p99 latency ≤ SLO
- No new customer reports arriving

Post-recovery steps:
- [ ] Post all-clear in incident channel and #general
- [ ] Disable any feature flags deployed as part of the bad change
- [ ] Close incident in PagerDuty
- [ ] **Open blameless postmortem within 3 business days** → [postmortem-template.md](postmortem-template.md)

---

## Key Links

| Resource | Link |
|---|---|
| Rollback runbook | [production-rollback-runbook.md](production-rollback-runbook.md) |
| Postmortem template | [postmortem-template.md](postmortem-template.md) |
| SEV2 runbook | [sev2-response.md](sev2-response.md) |
| Health endpoint | `GET /api/health/deep` |
| Metrics endpoint | `GET /api/metrics` |

---

## Downgrade to SEV2

Downgrade to SEV2 when impact narrows to a subset of users (error rate drops below 5%) and the path to resolution is clear. See [sev2-response.md](sev2-response.md).

---

*Last updated: 2026-06-18. Owner: on-call rotation.*
