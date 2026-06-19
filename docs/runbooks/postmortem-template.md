# Blameless Postmortem Template

<!-- anchor: postmortem-template -->

> **Required for:** every SEV1 and SEV2 incident, within 3 business days of resolution.
> Referenced from [sev1-response.md](sev1-response.md) and [sev2-response.md](sev2-response.md).

**Blame framing:** This postmortem is blameless. People made reasonable decisions given what they knew at the time. The goal is to surface systemic failures so the system improves — not to assign fault to individuals.

---

## Incident Header

| Field | Value |
|---|---|
| **Title** | _One sentence describing the incident_ |
| **Severity** | SEV1 / SEV2 |
| **Date** | YYYY-MM-DD |
| **Duration** | HH:MM (detection → all-clear) |
| **Incident Commander** | Name |
| **Ops Lead** | Name |
| **Scribe** | Name |
| **Services impacted** | `vecna-api`, `vecna-runner`, `vecna-db` (list those affected) |
| **Postmortem author** | Name |
| **Postmortem review date** | YYYY-MM-DD |

---

## Impact Scope

*Quantify. Vague impact statements prevent comparison across incidents.*

| Dimension | Value |
|---|---|
| User-facing error rate at peak | _e.g., 42%_ |
| Operations failed or incomplete | _count_ |
| Billing sessions affected | _count_ |
| Customer reports received | _count_ |
| Revenue impact (if known) | _$ or "unknown"_ |
| Data loss / corruption | _yes/no; describe if yes_ |

---

## Incident Timeline

List events in UTC. Include detection, each major action, and recovery. Be specific — "restarted the service at 14:32" beats "service was restarted."

| Time (UTC) | Event |
|---|---|
| HH:MM | Alert fired / first customer report |
| HH:MM | Alert acknowledged by on-call |
| HH:MM | SEV declared; IC assigned |
| HH:MM | _First investigation step_ |
| HH:MM | _Rollback initiated_ / _root cause identified_ |
| HH:MM | _Rollback confirmed good_ |
| HH:MM | Recovery criteria met — all-clear declared |
| HH:MM | Incident closed in PagerDuty |

---

## Detection & Response Timeline

Answer these explicitly:

- **How was the incident detected?** _(alert, customer report, engineer noticed)_
- **Time from first symptom to detection:** _MM min_
- **Time from detection to SEV declaration:** _MM min_
- **Time from detection to first mitigation action:** _MM min_
- **Time from first mitigation to recovery:** _MM min_
- **Were runbooks followed?** _(yes / no / partial — note gaps)_

---

## Root Cause Analysis

*Describe what broke and why. Use the five-whys method if causation is non-obvious.*

### What happened

_Technical narrative. No blame. Describe the system behaviour._

### Contributing factors

List each factor that made the incident worse, slower to detect, or harder to mitigate. These become candidates for action items.

- [ ] Factor 1 — _e.g., no automated health-check alert existed for the runner_
- [ ] Factor 2
- [ ] Factor 3

### What went well

*These reinforce good practices and should not be skipped.*

- _e.g., Rollback procedure was followed immediately and reduced impact within 8 min_
- 

---

## Action Items

Every action item must have an **owner** and a **due date**. Unowned items do not get done.

| # | Action | Owner | Due | Priority |
|---|---|---|---|---|
| 1 | _e.g., Add synthetic health-check alert for agent runner_ | @name | YYYY-MM-DD | High |
| 2 | | | | |
| 3 | | | | |

**Priority guide:**
- **High** — prevents recurrence of the same incident class
- **Medium** — reduces detection or response time
- **Low** — nice-to-have resilience improvement

---

## Anti-Blame Framing Checklist

Before publishing, verify:

- [ ] No individual is named as the cause of the incident
- [ ] All "who did X" statements focus on role, not person, or are removed
- [ ] Contributing factors are systemic (process, tooling, alert gaps) not personal
- [ ] Action items target the system, not individuals' judgment
- [ ] The document would be comfortable to share with the person who made the change that contributed to the incident

---

## Sign-Off

| Role | Name | Date |
|---|---|---|
| Postmortem author | | |
| Incident Commander | | |
| Engineering Lead | | |

---

*Template version: 2026-06-18. Maintained in `docs/runbooks/`. Questions → #incidents Slack.*
