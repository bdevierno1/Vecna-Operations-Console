# Rollback Runbook — Frontend (React / Vite)

**Service:** `frontend/` — React + TypeScript SPA built with Vite, served as static files or via `npm run dev`  
**Tier:** 0  
**Owner:** On-call engineer  
**Last tested:** see Git log on this file  
**Related alerts:** [SEV1 runbook](../sev1-runbook.md) · [SEV2 runbook](../sev2-runbook.md)

---

## 1. Rollback decision gate

Roll back the frontend when **any** of the following is true after a deploy:

| Signal | Threshold |
|--------|-----------|
| Blank white screen for >50 % of page loads | >2 min |
| JS console shows uncaught exception on app boot | any occurrence |
| WebSocket connection to `/ws/<op_id>` never established from UI | confirmed |
| Timeline or findings panel fails to render after operation completes | >5 % reports |
| URL submission returns no feedback (silent failure) | any occurrence |

> Backend health is unaffected by a frontend rollback. Roll back only the static assets or the Vite dev server.

---

## 2. Identify the previous artifact

### 2a. Production (static build served by a web server / CDN)

```bash
# The build artifact is dist/ committed or uploaded at deploy time.
# Find the previous deploy commit:
git -C /path/to/Vecna-Operations-Console log --oneline frontend/ -- | head -5
PREV_SHA=$(git -C /path/to/Vecna-Operations-Console log --oneline frontend/ -- | awk 'NR==2{print $1}')
echo "Rolling back to: $PREV_SHA"
```

### 2b. Development / Electron (Vite dev server)

The running process serves live source. Rolling back means checking out the previous commit and restarting Vite.

---

## 3. Rollback steps

### 3a. Static build rollback (production)

```bash
REPO=/path/to/Vecna-Operations-Console

# 1. Stop the current serving process or CDN deployment
# (steps depend on your hosting: nginx, Caddy, S3, etc.)

# 2. Check out the previous frontend commit
git -C "$REPO" checkout "$PREV_SHA" -- frontend/

# 3. Rebuild from the previous source
cd "$REPO/frontend"
npm install --silent
npm run build           # outputs to frontend/dist/

# 4. Re-deploy dist/ to your static host
#    e.g. cp -r dist/* /var/www/vecna/
#    or: aws s3 sync dist/ s3://your-bucket/ --delete

# 5. Hard-purge CDN cache if applicable
```

### 3b. Vite dev server rollback

```bash
REPO=/path/to/Vecna-Operations-Console

# 1. Kill the Vite process
pkill -f "vite" || true

# 2. Check out previous frontend source
git -C "$REPO" checkout "$PREV_SHA" -- frontend/

# 3. Install (in case package.json changed)
cd "$REPO/frontend"
npm install --silent

# 4. Restart Vite
npm run dev &
echo "Vite restarting on http://localhost:5173"
```

### 3c. Electron rollback

```bash
# 1. Kill the Electron process
pkill -f "electron" || true

# 2. Roll back frontend source (same as 3b above)
# 3. Rebuild frontend: cd frontend && npm run build
# 4. Restart Electron
cd /path/to/Vecna-Operations-Console/electron
npm start &
```

---

## 4. Verify rollback success

```bash
# 1. Frontend loads without JS errors
#    Open http://localhost:5173 (or your prod URL) in a browser
#    Open DevTools → Console → confirm no uncaught exceptions on load

# 2. Submit a test operation via the UI
#    Enter https://example.com → Submit
#    Confirm: timeline panel appears, events stream, findings render

# 3. Confirm WebSocket connects (DevTools → Network → WS tab)
#    Should see frames arriving within 5 s of submission

# 4. Confirm Electron window renders correctly (if Electron is in use)
```

---

## 5. Post-rollback actions

1. Announce in incident channel: "Frontend rolled back to `PREV_SHA`. Users should hard-refresh (Ctrl+Shift+R / Cmd+Shift+R)."
2. Identify which frontend change caused the regression (check `git diff $PREV_SHA HEAD -- frontend/`).
3. File a postmortem using [docs/postmortem-template.md](../../postmortem-template.md).
4. Add a smoke test or E2E check that would have caught the regression before deploy.

---

## 6. Rollback test log

| Date | Tester | Commit rolled from → to | Result | Notes |
|------|--------|------------------------|--------|-------|
| YYYY-MM-DD | | | | Drill — first entry |
