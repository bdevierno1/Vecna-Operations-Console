# Vecna Ops — beginner’s guide to the stack

This document explains **what each part of the project does**, **which packages you installed and why**, and **how files fit together**. It assumes you are new to full‑stack Python + web frontends.

**Want file-by-file detail?** The long, college-notes style walkthrough is **[FILE_BY_FILE.md](FILE_BY_FILE.md)** (every important source file, what it does, and how data flows through the system).

---

## 1. What you are building (one picture)

You have a **small security reconnaissance app**:

1. The **browser** shows a UI where you type a target URL.
2. The **frontend** (React) talks to the **backend** (FastAPI) over HTTP and WebSockets.
3. The backend runs an **AI agent** (Strands) that can call **tools** — Python functions that do DNS lookups, fetch HTTP headers, probe paths, etc.
4. The agent also calls an **LLM** (via LiteLLM) to reason and write a report.
5. Results are **saved in SQLite** and **streamed live** to the UI.

So: **React UI ↔ FastAPI ↔ Strands agent + tools ↔ LiteLLM ↔ cloud model API**.

---

## 2. Words you will see everywhere

| Term | Plain meaning |
|------|----------------|
| **Python** | Language the backend and agent use. |
| **Virtual environment (`.venv`)** | A folder that holds *this project’s* Python packages so they do not clash with other projects. |
| **`requirements.txt`** | List of Python packages to install with `pip install -r requirements.txt`. |
| **Node.js / npm** | Runtime and package manager for JavaScript/TypeScript (the frontend). |
| **`package.json`** | Frontend’s list of npm packages (like `requirements.txt` for JS). |
| **FastAPI** | Python library to build HTTP APIs (routes like `POST /api/operations`). |
| **Uvicorn** | Program that **runs** the FastAPI app (the actual web server process). |
| **Vite** | Dev tool that serves the React app during development and bundles it for production. |
| **React** | Library for building the interactive UI in the browser. |
| **TypeScript** | JavaScript with types; catches mistakes before runtime. |
| **WebSocket** | Long‑lived connection so the server can **push** log lines to the UI without the browser asking every second. |
| **SQLite** | File‑based database (`vecna.db`) — no separate database server. |
| **Strands** | Agent framework: wires an LLM + tools into a loop (think, call tool, think again…). |
| **LiteLLM** | Unified way to call many LLM providers (OpenAI, OpenRouter, Anthropic, etc.) with one API style. |
| **OpenRouter** | A service that routes requests to many models; you often use it with an `openrouter/...` model id. |

---

## 3. Backend Python packages (`backend/requirements.txt`)

Each line is a **dependency** — code someone else wrote that this project imports.

| Package | Role in Vecna Ops |
|---------|-------------------|
| **strands-agents** | Agent runtime: `@tool` functions, agent loop, telemetry hooks. |
| **httpx** | Modern HTTP **client** for the recon tools (GET requests, timeouts). Used by `app.services.http_client` for outbound scans. |
| **fastapi** | Web framework: routes, request/response models, WebSocket endpoints. |
| **uvicorn[standard]** | ASGI server that runs FastAPI. The `[standard]` extra adds helpers for production-ish features. |
| **sqlalchemy** | Database toolkit: models (`Operation`), async sessions, queries. |
| **greenlet** | Low-level helper SQLAlchemy/async sometimes needs. |
| **aiosqlite** | Async driver so FastAPI can use SQLite without blocking the event loop. |
| **pydantic-settings** | Loads `Settings` from environment variables and `.env` (`app/config.py`). |
| **sse-starlette** | Server-Sent Events support (if used for streaming patterns). |
| **litellm** | Calls the configured LLM using `LITELLM_MODEL_ID` and your API keys. |
| **openai** | Installed because LiteLLM often uses OpenAI-compatible APIs under the hood. |
| **pytest** | Test runner for `backend/tests/`. |

---

## 4. Frontend npm packages (`frontend/package.json`)

### Dependencies (ship with the app)

| Package | Role |
|---------|------|
| **react** / **react-dom** | UI library and DOM rendering. |
| **vite** | Dev server + build (in `devDependencies` but central to the workflow). |
| **@vitejs/plugin-react** | Lets Vite compile React + JSX. |
| **typescript** | Type checking for `.ts` / `.tsx` files. |
| **tailwindcss** + **@tailwindcss/vite** | Utility-first CSS; you style with class names. |
| **@radix-ui/react-*** | Accessible UI primitives (scroll area, button slots, etc.). |
| **lucide-react** | Icon set. |
| **clsx**, **tailwind-merge**, **class-variance-authority** | Helpers for combining Tailwind class strings cleanly. |

### Dev-only

| Package | Role |
|---------|------|
| **eslint**, **typescript-eslint**, plugins | Lint rules for code quality. |

The **Vite proxy** (`frontend/vite.config.ts`) forwards `/api` and `/ws` to `http://127.0.0.1:8000` so the React app can call the backend without CORS pain during development.

---

## 5. Optional: Electron (`electron/`)

There is a small **Electron** wrapper (`electron/package.json`) — a way to package a web UI as a desktop shell. It is **not required** for normal dev: most people run **Vite + FastAPI** only.

---

## 6. Repository layout (files and folders)

### Root

| Path | Purpose |
|------|---------|
| **`README.md`** | Quick start, env vars, layout table. |
| **`docs/PROJECT_GUIDE.md`** | This file — deeper explanations. |
| **`.gitignore`** | Tells Git to ignore secrets (`backend/.env`), `node_modules`, `.venv`, `vecna.db`, build output. |

### Backend — `backend/`

| Path | Purpose |
|------|---------|
| **`app/main.py`** | FastAPI app: REST routes, WebSocket hub, operation lifecycle, config endpoint. |
| **`app/config.py`** | Settings from `.env` (model id, API keys, CORS, pricing toggles). |
| **`app/database.py`** | SQLite engine + session factory. |
| **`app/models.py`** | SQLAlchemy models (e.g. `Operation` rows). |
| **`app/hub.py`** | WebSocket connection registry for streaming events to clients. |
| **`app/url_guard.py`** | Safety checks on user-supplied URLs (e.g. block obviously unsafe targets). |
| **`app/schema_migrate.py`** | Lightweight SQLite column migrations. |
| **`app/services/`** | “Business logic”: billing sessions, pricing, token cost estimates, telemetry, HTTP client for tools, rate limits, **runner** (executes one operation), **operation_context** (tool hooks). |
| **`app/lib/`** | Small shared helpers (`serialize` for streams, `report` for structured export). |
| **`agent/vecna_agent.py`** | Builds the Strands `Agent` with your tools and model. |
| **`agent/vecna_litellm.py`** | LiteLLM model adapter for Strands. |
| **`agent/tools.py`** | The three recon `@tool` functions (see below). |
| **`agent/tool_hooks.py`** | Wraps tools for pre/post logging and billing side effects. |
| **`.env` / `.env.example`** | Secrets and configuration (copy example to `.env` and fill keys). |
| **`vecna.db`** | SQLite file (created at runtime; gitignored). |
| **`tests/`** | Pytest tests for services and API behavior. |

### Frontend — `frontend/src/`

| Path | Purpose |
|------|---------|
| **`main.tsx`** | React entry: mounts the app into the HTML page. |
| **`App.tsx`** | Main UI: URL form, timeline, findings, operations list, WebSocket handling. |
| **`components/ui/*`** | Reusable styled pieces (buttons, cards, inputs) using Radix + Tailwind. |
| **`lib/utils.ts`** | Small helpers (e.g. `cn()` for class names). |
| **`lib/streamMerge.ts`** | Logic to merge streamed events for display. |

---

## 7. The three agent tools (what the LLM can “call”)

Defined in **`backend/agent/tools.py`** and registered in **`vecna_agent.py`**. Each is a **passive** recon step (no exploitation).

| Tool | What it does |
|------|----------------|
| **`resolve_dns_and_subdomains`** | DNS resolution for the host; uses **crt.sh** to discover certificate transparency names related to the domain. Returns JSON. |
| **`analyze_http_security_headers`** | HTTP GET to the target; inspects headers (HSTS, CSP, X-Frame-Options, etc.) and returns structured findings. |
| **`probe_common_paths`** | GETs a **fixed small list** of paths (`/robots.txt`, `/.well-known/security.txt`, etc.) — not a brute-force scanner. |

Tools use **`recon_client()`** from **`app.services.http_client`** so every outbound request gets consistent timeouts, user-agent, and logging.

---

## 8. Typical commands you will run

| Goal | Command |
|------|---------|
| Backend venv + install | `cd backend && python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt` |
| Run API | `uvicorn app.main:app --reload --host 0.0.0.0 --port 8000` (from `backend/` with venv active) |
| Run tests | `pytest tests/ -q` (from `backend/`) |
| Frontend install | `cd frontend && npm install` |
| Frontend dev | `npm run dev` → opens Vite, usually **http://localhost:5173** |

---

## 9. Environment variables (mental model)

- **`LITELLM_MODEL_ID`** — Which model LiteLLM calls (e.g. `openrouter/...` or `openai/gpt-4o-mini`).
- **`OPENROUTER_API_KEY`** / **`OPENAI_API_KEY`** / others — Must match the **provider** implied by the model id prefix.
- **`CORS_ORIGINS`** — Browser origins allowed to talk to the API (includes the Vite dev URL by default).
- **Pricing knobs** — `VECNA_*` variables described in **`README.md`**; they add optional platform fees on top of LLM usage.

Never commit **`backend/.env`**; it is in **`.gitignore`**. Share **`.env.example`** instead (no real secrets).

---

## 10. If something breaks

1. **Backend not running** — Frontend will fail API/WebSocket calls; start Uvicorn first.
2. **401/403 from LLM** — Model id and API key mismatch (e.g. OpenRouter model without OpenRouter key).
3. **Port in use** — Change Uvicorn port or stop the other process.
4. **Stale UI** — Hard refresh; check browser devtools **Network** and **Console** for errors.

---

## 11. Where to learn more (official docs)

- [FastAPI](https://fastapi.tiangolo.com/) — tutorials and async explanation.
- [Vite](https://vite.dev/) — config, proxy, build.
- [React](https://react.dev/) — components and hooks.
- [LiteLLM](https://docs.litellm.ai/) — providers and model strings.
- [Strands Agents](https://strandsagents.com/latest/documentation/docs/user-guide/quickstart/python/) — agent loop, `@tool`, telemetry.

This project is a **learning-friendly slice** of a real stack: you can trace one user action from `App.tsx` → `POST /api/...` → `runner` → `Agent` → `tools.py` and back over the WebSocket.
