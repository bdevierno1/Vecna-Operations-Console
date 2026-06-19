# Rollback Runbook — Agent Runner (Strands / LiteLLM)

**Service:** `backend/agent/` + `backend/app/services/runner.py` — the async task that executes each reconnaissance operation  
**Tier:** 0  
**Owner:** On-call engineer  
**Last tested:** see Git log on this file  
**Related alerts:** [SEV1 runbook](../sev1-runbook.md) · [SEV2 runbook](../sev2-runbook.md)

---

## 1. Rollback decision gate

Roll back when **any** of the following is true for >5 min after a deploy:

| Signal | Threshold |
|--------|-----------|
| Operations stuck in `running` status with no events emitted | >3 concurrent |
| `execute_operation` raises unhandled exception (visible in logs) | any occurrence |
| LLM auth errors (`401`/`403`) caused by a key/model config regression | confirmed |
| Tool hook failures causing operations to hard-fail on start | >2 in 5 min |
| WebSocket clients receive `error` frame before first tool event | >5 % of ops |

> **Note:** LLM-provider outages are *not* rollback triggers — they are provider incidents. Distinguish by checking `GET /api/health/deep` (`model` field) before rolling back.

---

## 2. Identify the previous artifact

The agent runner is co-deployed with the API server (same Python process). The rollback artifact is the same **Git commit** as the API server.

```bash
# Confirm current running version
git -C /path/to/Vecna-Operations-Console log --oneline -3

# Identify the commit before the bad deploy
PREV_SHA=$(git -C /path/to/Vecna-Operations-Console log --oneline | awk 'NR==2{print $1}')
echo "Rolling back to: $PREV_SHA"
```

---

## 3. Cancel stuck in-flight operations (before rollback)

```bash
# List operations currently in 'running' state via API
curl -s http://127.0.0.1:8000/api/operations?status=running | jq '.[] | {id:.id, created_at:.created_at}'

# Cancel each stuck operation
OP_ID="<operation-id>"
curl -s -X DELETE http://127.0.0.1:8000/api/operations/$OP_ID/cancel | jq .
```

> Cancellation sets the row to `error` status and closes the WebSocket stream cleanly. Users may retry after rollback.

---

## 4. Rollback steps

The agent runner lives in the same process as the API server. Follow **[api-server-rollback.md](api-server-rollback.md) §3** to restart on the previous commit. Additional agent-specific steps:

```bash
# After restarting on PREV_SHA, confirm the agent config is intact
curl -s http://127.0.0.1:8000/api/config | jq '{model:.model_id, pricing:.tool_pricing}'

# If a dependency (strands-agents, litellm) version was the root cause,
# pin the previous working version explicitly before restarting:
REPO=/path/to/Vecna-Operations-Console/backend
source "$REPO/.venv/bin/activate"
pip install "strands-agents==<PREV_KNOWN_GOOD>" "litellm==<PREV_KNOWN_GOOD>" --quiet
```

---

## 5. Verify rollback success

```bash
# 1. Start a real operation (use a safe public target)
OP=$(curl -s -X POST http://127.0.0.1:8000/api/operations \
  -H "Content-Type: application/json" \
  -d '{"target_url":"https://example.com"}' | jq -r '.id')
echo "Operation: $OP"

# 2. Poll for the first tool event (should appear within 30 s)
for i in $(seq 1 12); do
  STATUS=$(curl -s http://127.0.0.1:8000/api/operations/$OP | jq -r '.status')
  echo "$(date +%T) status=$STATUS"
  [ "$STATUS" = "running" ] && break
  sleep 5
done

# 3. Connect via WebSocket and verify events stream (requires wscat or similar)
# wscat -c ws://127.0.0.1:8000/ws/$OP
# Expect: tool_start events within 30 s, done/error event at end.

# 4. Confirm operation reaches 'done' or 'error' (not stuck in 'running')
sleep 120
curl -s http://127.0.0.1:8000/api/operations/$OP | jq '{status:.status, findings_count: (.findings_json | length)}'
```

---

## 6. Post-rollback actions

1. Announce in incident channel: "Agent runner rolled back to `PREV_SHA`. All new operations use previous agent version."
2. Check LiteLLM and strands-agents changelogs for breaking changes if a package upgrade caused the incident.
3. File a postmortem using [docs/postmortem-template.md](../../postmortem-template.md).
4. Update "Last tested" date below after next successful drill.

---

## 7. Rollback test log

| Date | Tester | Commit rolled from → to | Result | Notes |
|------|--------|------------------------|--------|-------|
| YYYY-MM-DD | | | | Drill — first entry |
