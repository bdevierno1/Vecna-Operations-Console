# Vecna Operations Console

<img width="1397" height="902" alt="image" src="https://github.com/user-attachments/assets/8607ca09-161d-4761-90fc-1def6ee85802" />

<img width="1397" height="871" alt="image" src="https://github.com/user-attachments/assets/68a4d1f6-9b22-4b35-89c4-ce55c1805ba7" />


Take-home style stack: **Strands** agent (Python), **FastAPI** backend, **React + TypeScript** frontend. Passive reconnaissance against a user-supplied URL with live streaming, findings, credits, and persisted operations.

**New to this stack?** See **[docs/PROJECT_GUIDE.md](docs/PROJECT_GUIDE.md)** for packages, folders, and vocabulary. For **line-by-line, file-by-file** explanations (meant for first-time learners), use **[docs/FILE_BY_FILE.md](docs/FILE_BY_FILE.md)**. For **how this repo implements** cost, retries, HTTP client, telemetry, rate limits, streaming, history, health, and WebSockets, see **[docs/CAPABILITIES.md](docs/CAPABILITIES.md)**. For a **single narrative** on how the app works end-to-end (essay style), see **[docs/ARCHITECTURE_ESSAY.md](docs/ARCHITECTURE_ESSAY.md)**.

## Repository layout

| Path | Role |
|------|------|
| `backend/app/main.py` | FastAPI app, HTTP + WebSocket routes |
| `backend/app/` | Core: `config`, `database`, `models`, `hub`, `url_guard`, `schema_migrate` |
| `backend/app/services/` | Domain logic: billing, pricing, cost, telemetry, HTTP client, errors, rate limits, operation runner, tool lifecycle context |
| `backend/app/lib/` | Shared helpers: stream `serialize`, structured `report` |
| `backend/agent/` | Strands agent, LiteLLM adapter, recon tools + hooks |
| `backend/tests/` | Pytest |
| `frontend/src/` | Vite + React UI (`App.tsx`, `components/ui`, `lib`) |
| `docs/` | Learning docs (`PROJECT_GUIDE.md`, `FILE_BY_FILE.md`, `CAPABILITIES.md`, `ARCHITECTURE_ESSAY.md`) |
| `electron/` | Optional **desktop window** (Electron); see `electron/README.md` |

## Desktop window (Electron)

Use the same UI in a native window (recommended workflow: backend + Vite + Electron):

1. Backend: `uvicorn` on port **8000** (see Backend above).
2. Frontend: `cd frontend && npm run dev` (Vite on **5173**).
3. Electron: `cd electron && npm install && npm start`.

Details, env vars (`ELECTRON_OPEN_DEVTOOLS`, built `dist/` + `VITE_API_ORIGIN`), and CORS notes: **[electron/README.md](electron/README.md)**.

## Prerequisites

- Python 3.12+ (recommended)
- Node 20+
- An LLM key — **OpenRouter** is the default path in `.env.example` (set `OPENROUTER_API_KEY` + `LITELLM_MODEL_ID=openrouter/...`), or use `openai/...` with `OPENAI_API_KEY`.

## Backend

```bash
cd vecna-ops/backend
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env        # edit: keys + model id
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

- Health: `GET http://127.0.0.1:8000/health`
- API: `http://127.0.0.1:8000/api/operations`
- Structured report export: `GET /api/operations/{id}/report.json`

### Platform & tool pricing (optional)

Billable total = **LLM/token $** (from the model) + optional **platform** charges. Configure in `backend/.env` (see `.env.example`).

| Variable | Meaning |
|----------|---------|
| `VECNA_SCAN_FEE_USD` | Flat fee per **completed** operation (successful scan). |
| `VECNA_TOOL_UNIT_FEE_USD` | Legacy: same **$/tool call** for every tool (simplest). Ignored for tool $ if tiered JSON is set and the legacy path does not apply — prefer one style. |
| `VECNA_TOOL_FAMILY_FEES_JSON` | Tiered: **$/call** by family. Example: `{"dns":0.005,"network":0.01,"paths":0.01,"default":0}`. Built-in tools map to `dns` / `network` / `paths` (see `app/services/pricing.py`). |
| `VECNA_TOOL_FAMILY_BY_TOOL_JSON` | Override which **family** a tool name uses (JSON map). |
| `VECNA_TOOL_FEE_OVERRIDES_JSON` | **$/call** for a specific tool name (overrides family). Example: `{"probe_common_paths":0.25}`. |
| `VECNA_HIDE_COSTS` | If `true`, hide dollar amounts in the UI (tokens still shown). |

**Examples**

1. **Scan only:** `VECNA_SCAN_FEE_USD=0.10` — adds $0.10 per finished run; tool rows can stay $0.
2. **Flat tool surcharge:** `VECNA_TOOL_UNIT_FEE_USD=0.01` — $0.01 × (number of tool calls), all tools.
3. **By family:** set `VECNA_TOOL_FAMILY_FEES_JSON` as above. If **any** of the tiered JSON env vars is non-empty, the legacy `VECNA_TOOL_UNIT_FEE_USD` path is **not** used (see `compute_tool_fees` in `app/services/pricing.py`).

Restart the backend after changing `.env`. `GET /api/config` exposes non-secret pricing hints for the frontend.

### Tests

```bash
cd vecna-ops/backend
# Optional: ensure backend/.env does not set DATABASE_URL if you want in-memory DB for tests
pytest tests/ -q
```

## Frontend

```bash
cd vecna-ops/frontend
npm install
npm run dev
```

Opens Vite on **http://localhost:5173** with a dev proxy to the API/WebSocket on port **8000**.

## What it does

1. **Agent** — Three custom tools: DNS + crt.sh names, HTTP security headers, bounded path probes; structured markdown report at the end.
2. **Backend** — Creates operations, runs the agent in the background, streams JSON events over WebSockets (with human-readable `human_line` fields), estimates credits (tool calls + LLM cycles), stores SQLite rows.
3. **Frontend** — Submit URL, live timeline (readable lines + expandable raw JSON), findings, credits, history, and **Download report.json** for the selected operation.

## Troubleshooting

- **Port 8000 in use**: stop the other process or pick another port for uvicorn.
- **LLM auth errors**: confirm `LITELLM_MODEL_ID` matches your key (`openrouter/...` + `OPENROUTER_API_KEY`, or `openai/...` + `OPENAI_API_KEY`).
- **CORS**: `CORS_ORIGINS` in `.env` should include your frontend origin (default includes `http://localhost:5173`).
