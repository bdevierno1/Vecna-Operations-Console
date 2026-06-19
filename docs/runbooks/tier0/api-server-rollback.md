# Rollback Runbook — API Server (FastAPI / Uvicorn)

**Service:** `backend/app/main.py` — FastAPI application served by Uvicorn on port 8000  
**Tier:** 0  
**Owner:** On-call engineer  
**Last tested:** see Git log on this file  
**Related alerts:** [SEV1 runbook](../sev1-runbook.md) · [SEV2 runbook](../sev2-runbook.md)

---

## 1. Rollback decision gate

Roll back when **any** of the following is true for >5 min after a deploy:

| Signal | Threshold |
|--------|-----------|
| `GET /health` returns non-200 | sustained |
| `GET /api/health/deep` reports DB or model failure | sustained |
| Operation creation (`POST /api/operations`) error rate | >10 % |
| WebSocket stream silently drops mid-run | >2 reports |
| 5xx rate on any route | >5 % over 5-min window |

If you are unsure, roll back first and investigate on the previous artifact.

---

## 2. Identify the previous artifact

The "artifact" for this service is the **Git commit / container image** running in production.

### 2a. Direct process (no container)

```bash
# Running process — what commit is it?
curl -s http://127.0.0.1:8000/api/health/deep | jq .
# Check the commit the process was started from
ps aux | grep uvicorn
# Find prior commit
git -C /path/to/repo log --oneline -5
```

Note the commit SHA immediately before the bad deploy — call it `PREV_SHA`.

### 2b. Container / image (Docker)

```bash
# List recent images tagged for this service
docker images vecna-api --format "{{.Tag}}\t{{.CreatedAt}}" | head -10
# Identify the image before the current one
PREV_IMAGE="vecna-api:<previous-tag>"
```

---

## 3. Rollback steps

### 3a. Direct process rollback

```bash
# 1. Identify where the repo lives on the host
REPO=/path/to/Vecna-Operations-Console/backend

# 2. Kill the running Uvicorn process gracefully
PID=$(pgrep -f "uvicorn app.main:app")
kill -SIGTERM "$PID"
sleep 3

# 3. Check out the previous good commit
git -C "$REPO/.." checkout "$PREV_SHA"

# 4. Re-activate venv and reinstall dependencies (in case requirements changed)
source "$REPO/.venv/bin/activate"
pip install -r "$REPO/requirements.txt" --quiet

# 5. Restart Uvicorn
cd "$REPO"
uvicorn app.main:app --host 0.0.0.0 --port 8000 &

# 6. Verify health
sleep 3
curl -s http://127.0.0.1:8000/health && echo " OK"
curl -s http://127.0.0.1:8000/api/health/deep | jq '{db: .database, model: .model}'
```

### 3b. Container rollback

```bash
# 1. Stop the running container
docker stop vecna-api-current

# 2. Start the previous image
docker run -d --name vecna-api \
  --env-file /etc/vecna/.env \
  -p 8000:8000 \
  "$PREV_IMAGE"

# 3. Verify
sleep 5
curl -s http://127.0.0.1:8000/health && echo " OK"
```

---

## 4. Verify rollback success

All of these must pass before the incident is resolved:

```bash
# Health check — expect HTTP 200 with {"status":"ok"}
curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8000/health

# Deep health — database and model connectivity
curl -s http://127.0.0.1:8000/api/health/deep | jq .

# Smoke test: start a trivial operation and confirm it enters running state
curl -s -X POST http://127.0.0.1:8000/api/operations \
  -H "Content-Type: application/json" \
  -d '{"target_url":"https://example.com"}' | jq '{id:.id, status:.status}'

# Confirm metrics endpoint responds
curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8000/api/metrics
```

Expected: all return 200; operation row transitions to `running` within 5 s.

---

## 5. Post-rollback actions

1. Announce in incident channel: "API server rolled back to `PREV_SHA`. Monitoring for 30 min."
2. File a postmortem issue using [docs/postmortem-template.md](../../postmortem-template.md).
3. Do NOT re-deploy the bad commit until root cause is identified and a fix is committed.
4. Update this file's "Last tested" date after the next successful planned rollback drill.

---

## 6. Rollback test log

| Date | Tester | Commit rolled from → to | Result | Notes |
|------|--------|------------------------|--------|-------|
| YYYY-MM-DD | | | | Drill — first entry |
