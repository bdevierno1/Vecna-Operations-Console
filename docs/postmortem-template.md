# Blameless Postmortem Template

> **How to use this template:** Copy this file to `docs/postmortems/YYYY-MM-DD-<slug>.md`, fill every section, and open it as a PR within 3 business days of a SEV1 or SEV2 incident. Systems fail; people do their best with the information available. This document focuses on what happened and how to prevent recurrence — not on who made a mistake.

---

## Incident summary

| Field | Value |
|-------|-------|
| **Title** | `<short descriptive title, e.g. "API server down — migration removed a required column">` |
| **Date / time (UTC)** | `YYYY-MM-DD HH:MM – HH:MM UTC` |
| **Duration** | `<X h Y min>` |
| **Severity** | `SEV1` / `SEV2` |
| **Incident commander** | `<name>` |
| **Scribe** | `<name>` |
| **Participants** | `<names>` |
| **Affected service(s)** | `<e.g. API Server, Agent Runner>` |
| **Ticket / incident channel** | `#inc-YYYYMMDD-<slug>` |

---

## Impact

_Quantify the impact. Be precise._

- **Operations affected:** `<N>` operations failed to start / complete during the window.
- **Users affected:** `<N>` users experienced errors or degraded experience.
- **Data lost:** `<none / describe if any>`
- **External visibility:** `<yes/no — was this visible to users? How did they notice?>`

---

## Timeline

_List events in UTC. Include detection, escalation, key actions, and resolution. One line per event._

| Time (UTC) | Event |
|------------|-------|
| HH:MM | Alert fired / user reported issue |
| HH:MM | On-call acknowledged |
| HH:MM | SEV declared |
| HH:MM | Diagnosis: root cause identified |
| HH:MM | Mitigation applied (rollback / config change / restart) |
| HH:MM | Service restored; verification checks passed |
| HH:MM | Incident closed |

---

## Root cause

_One to three sentences. What was the direct technical cause? Avoid blame language._

Example: "A schema migration in `schema_migrate.py` dropped the `findings_json` column instead of adding it, causing all reads of `Operation.findings_json` to raise `OperationalError`."

```
<root cause here>
```

---

## Contributing factors

_What conditions allowed the root cause to have this impact? Use "5 Whys" or similar._

1. **Why did this happen?** `<e.g. The migration was not tested against an existing non-empty database.>`
2. **Why was it not caught before deploy?** `<e.g. CI only tests against a fresh schema; no migration regression test existed.>`
3. **Why did detection take N minutes?** `<e.g. The liveness check probes /health, which does not exercise the DB schema.>`
4. (Add more as needed)

---

## What went well

_Genuinely good practices that limited impact or aided recovery._

- `<e.g. The deep health endpoint surfaced the database failure within 2 minutes.>`
- `<e.g. The on-call engineer had the rollback runbook open and executed it in under 10 minutes.>`

---

## What went wrong

_Things that made this harder than it needed to be._

- `<e.g. No database backup existed; rollback required a schema-only repair.>`
- `<e.g. The SEV1 runbook lacked a step for cancelling stuck in-flight operations.>`

---

## Where we got lucky

_Things that could have made this worse but didn't._

- `<e.g. The incident occurred during low-traffic hours; only 3 users were affected.>`
- `<e.g. The SQLite file was not corrupted — only the schema was wrong.>`

---

## Action items

_Each item has an owner and a due date. Track these as issues in the repo._

| # | Action | Owner | Due date | Issue |
|---|--------|-------|----------|-------|
| 1 | `<e.g. Add migration regression test that runs against an existing DB fixture>` | `<name>` | `YYYY-MM-DD` | `#<n>` |
| 2 | `<e.g. Add PRAGMA integrity_check to the /api/health/deep endpoint>` | `<name>` | `YYYY-MM-DD` | `#<n>` |
| 3 | `<e.g. Set up automated 15-minute SQLite backups per database-rollback.md §6>` | `<name>` | `YYYY-MM-DD` | `#<n>` |

---

## Detection gap

_How long did it take to detect? What would have caught this faster?_

- **Time to detect:** `<N min>`
- **Alert that fired (or didn't):** `<e.g. A02 deep health — but only after 2-min threshold>`
- **Missing alert:** `<e.g. No alert for schema column presence; adding A05 integrity check would have fired sooner>`

---

## Appendix: supporting data

_Paste relevant log snippets, queries, and metrics here._

```
<paste log lines, stack traces, or SQL output>
```

---

_Filed by:_ `<name>` on `YYYY-MM-DD`  
_Reviewed by:_ `<names>` on `YYYY-MM-DD`
