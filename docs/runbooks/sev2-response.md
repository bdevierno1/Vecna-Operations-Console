# SEV2 Incident Response Runbook

<!-- anchor: runbook-sev2 -->

> **Alert annotation URL:** `docs/runbooks/sev2-response.md#runbook-sev2`
> Embed this anchor in PagerDuty / Alertmanager annotations so on-call lands here directly.

---

## Definition

**SEV2 = major degradation affecting a large subset of users; no total outage.**

Triggering conditions (any one is sufficient):
- Error rate > 5% sustained for > 5 min (but < 25% — above that → SEV1)
- p99 latency > 3× SLO for > 10 min
- Agent runner failing for ≥ 20% of new operations
- WebSocket streaming broken for a significant portion of clients
- Billing / cost calculations producing wrong results affecting multiple sessions

If impact expands to a full outage, **upgrade to SEV1** immediately — see [sev1-response.md](sev1-response.md).

---

## Immediate Actions (first 5 minutes)

SLA: acknowledge the alert within **5 minutes** of detection.

```
[ ] Acknowledge alert in PagerDuty
[ ] Post "SEV2 DECLARED — <brief symptom>" in #incidents Slack
[ ] Assign Incident Commander
[ ] Assign Ops Lead
```

**First technical check — in this order:**

1. **Recent deploy?** → Default action is rollback.
   ```bash
   git -C /opt/vecna-ops log --oneline -5
   # Correlate top commit timestamp with incident start
   # If match → rollback per production-rollback-runbook.md
   ```

2. **Single endpoint or service degraded?**
   ```bash
   # Check health breakdown
   curl -sf http://localhost:8000/api/health/deep | jq .
   # Identify which subsystem reports unhealthy
   ```

3. **LLM provider degraded?** (Agent failures only)
   ```bash
   # Check provider status page
   # If provider-side: add a user-visible banner, do not page further
   # Rate-limit new operations to reduce cost burn during degradation
   ```

> **Rule: stabilize before diagnosing.** A rollback that restores 80% of traffic is better than 30 minutes of in-place debugging.

---

## IC Responsibilities During a SEV2

The **Incident Commander** owns:
- Incident channel and role assignment
- **Hourly updates** to engineering lead (not exec unless it escalates)
- Declaring recovery

**Hourly update template:**
```
SEV2 UPDATE [HH:MM UTC]
Status: <investigating | mitigating | resolved>
Impact: <what is degraded / estimated % of users affected>
Last action: <what was just done>
Next action: <what is happening next>
```

---

## Escalation

| Time since declare | Action |
|---|---|
| 0 min | Page on-call engineer |
| 15 min | Page engineering lead if no active mitigation |
| 30 min | Reassess severity; upgrade to SEV1 if impact grows |
| 60 min | First hourly update to engineering lead |

---

## Recovery Criteria

Do not declare recovery until **all** hold for ≥ 15 min:

- Error rate ≤ baseline
- p99 latency ≤ SLO
- `GET /api/health/deep` fully healthy
- No new reports of the degraded behaviour

Post-recovery steps:
- [ ] Post all-clear in #incidents
- [ ] Close incident in PagerDuty
- [ ] **Open blameless postmortem within 3 business days** → [postmortem-template.md](postmortem-template.md)

---

## Key Links

| Resource | Link |
|---|---|
| Rollback runbook | [production-rollback-runbook.md](production-rollback-runbook.md) |
| Postmortem template | [postmortem-template.md](postmortem-template.md) |
| SEV1 runbook | [sev1-response.md](sev1-response.md) |
| Health endpoint | `GET /api/health/deep` |
| Metrics endpoint | `GET /api/metrics` |

---

## Downgrade to SEV3

Downgrade to SEV3 when a workaround exists, impact is localised, and the fix can ship in the next business-hours window. SEV3 goes into the ticket tracker, not PagerDuty.

---

*Last updated: 2026-06-18. Owner: on-call rotation.*
