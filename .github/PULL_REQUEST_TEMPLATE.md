## What & Why

<!-- One paragraph: what changed and why. Link the related issue/story. -->

Closes #

## How to verify

<!-- Step-by-step instructions a reviewer can follow to confirm the change works. -->

1.
2.

## Risk & rollback

<!-- What could go wrong? How is this reverted if it fails in production? -->

- **Risk:**
- **Rollback:**

---

## Release-Readiness Checklist

> Complete **every** item before requesting review. Items marked ⛔ are
> merge-blocking — the PR cannot land until they are resolved.

### CI & Quality

- [ ] ⛔ All CI checks pass (build, tests, lint, type-check)
- [ ] ⛔ Test coverage meets the bar; no new `skip`/`xfail` markers without a linked ticket
- [ ] ⛔ PR diff is under 400 lines — split if larger
- [ ] ⛔ No plaintext secrets or credentials in the diff

### Code Correctness

- [ ] ⛔ No known correctness bugs introduced
- [ ] ⛔ New behaviour is covered by at least one test
- [ ] ⛔ Irreversible changes (schema drops, data migrations) have a documented rollback path

### Security

- [ ] ⛔ No new security gaps (injection, auth bypass, exposed endpoints)
- [ ] Input validated and sanitised where applicable
- [ ] Sensitive operations are audit-logged

### Database Migrations

- [ ] Migration follows expand → migrate → contract pattern
- [ ] Migration is backward-compatible with the previous deploy artifact
- [ ] Rollback migration written and tested locally

### Feature Flags & Progressive Rollout

- [ ] Risky changes are behind a feature flag (default **off**)
- [ ] Kill switch is available and tested
- [ ] Rollout plan: canary → % rollout → 100%

### Operational Readiness

- [ ] Dashboards updated / new metrics added
- [ ] Alerts configured for new failure modes
- [ ] Runbook updated or created
- [ ] On-call team briefed if behaviour changes

### Documentation & Acceptance

- [ ] API docs / OpenAPI spec updated (if endpoints changed)
- [ ] Release notes / CHANGELOG entry added
- [ ] Acceptance criteria from the story verified
- [ ] Stakeholder / PM sign-off recorded (paste link or @mention below)

**Sign-off:** <!-- @mention or link to approval -->
