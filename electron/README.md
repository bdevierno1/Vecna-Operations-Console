# Vecna Ops — Electron desktop window

This folder runs the **same React UI** as the browser, inside a native window (Chromium via Electron).

## Recommended: dev server (hot reload)

You need **three** processes:

1. **Backend** — from `vecna-ops/backend` with venv active:

   ```bash
   uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
   ```

2. **Vite** — from `vecna-ops/frontend`:

   ```bash
   npm run dev
   ```

3. **Electron** — from `vecna-ops/electron` (install once: `npm install`):

   ```bash
   npm start
   ```

Electron loads **`http://localhost:5173`**. Vite proxies `/api` and `/ws` to the backend, so you do **not** set `VITE_API_ORIGIN` in this mode.

Optional:

- `ELECTRON_START_URL=http://127.0.0.1:5173 npm start` — if you bind Vite elsewhere.
- `ELECTRON_OPEN_DEVTOOLS=1 npm start` — open Chromium DevTools on launch.

## Optional: load built files (no Vite)

Use this when you want the window to open **without** running `npm run dev`.

1. Build the frontend with an API origin (no Vite proxy on disk):

   ```bash
   cd ../frontend
   echo 'VITE_API_ORIGIN=http://127.0.0.1:8000' > .env.production.local
   npm run build
   ```

2. Start the backend on port 8000 (same as above).

3. From `electron/`:

   ```bash
   npm run start:dist
   ```

This runs with `ELECTRON_USE_DEV_SERVER=0`, so Electron loads `../frontend/dist/index.html`. All `fetch`/`WebSocket` calls go to `http://127.0.0.1:8000` via `VITE_API_ORIGIN`.

Ensure `CORS_ORIGINS` in `backend/.env` includes nothing blocking `null` / file origins if you hit CORS issues—in practice Electron `file://` may send `Origin: null`; FastAPI CORS can allow specific origins; you may need to add `null` or use only the dev-server workflow for local testing.

For production hardening, package with **electron-builder** or similar (not configured in this repo yet).
