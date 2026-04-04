# Vecna Ops — file-by-file guide (learning edition)

This document walks through **almost every project-owned file** and explains **what it is**, **why it exists**, and **how it connects to neighbors**. It is written for someone seeing a full-stack Python + React repo for the first time—assume you know basic programming (variables, functions) but not necessarily HTTP, async, or build tools.

**How to use it**

- Read **§1–2** once for orientation.
- Skim the **directory sections**; drill into a file when you are debugging or changing that area.
- When you see jargon (ORM, WebSocket, …), there is a short explanation inline or in **§3**.

**Related:** [`PROJECT_GUIDE.md`](PROJECT_GUIDE.md) is the shorter “packages + folders” tour.

---

## 1. Mental model: one user click, end-to-end

Imagine you paste `https://example.com` into the UI and press Run.

1. **Browser** loads the React app from Vite (`frontend`).
2. **`App.tsx`** sends `POST /api/operations` with the URL (and maybe a billing session id).
3. **`main.py`** validates the URL with **`url_guard`**, inserts a row in SQLite via **`models` + `database`**, starts **`asyncio.create_task(execute_operation(...))`**, returns the new operation **id** immediately.
4. **`runner.execute_operation`** (background task) builds a Strands **`Agent`** in **`vecna_agent`**, streams LLM + tool events, pushes JSON messages through **`hub`**, and persists blobs on **`Operation`**.
5. The browser opens **`WebSocket /ws/operations/{id}`**; **`main.py`** replays stored events if any, then **`hub`** queues live events until “done” or “error”.
6. **`serialize.py`** turns noisy Strands events into smaller JSON + a **`human_line`** string for the timeline.
7. When the agent finishes, **`runner`** writes final costs, findings, summary; **`billing`** may update a **`BillingSession`** row.

You do not need to memorize this—come back when something “should have happened” and trace forward or backward.

---

## 2. Glossary (quick refresher)

| Term | One sentence |
|------|----------------|
| **Module** | A `.py` or `.ts` file that other files `import`. |
| **Package / folder** | A directory treated as a namespace (e.g. `app.services`). |
| **`__init__.py`** | Marks a Python directory as importable; can be empty. |
| **ORM (SQLAlchemy)** | Describe tables as Python classes; generate SQL for you. |
| **Async / `async def`** | Functions that can `await` I/O without blocking the whole server. |
| **WebSocket** | Long-lived TCP connection; server pushes messages anytime. |
| **REST / HTTP API** | `GET`/`POST` URLs returning JSON—request/response per call. |
| **ORM session** | A unit of work: load rows, change them, `commit()` to disk. |
| **Environment variables** | Key/value config (often in `.env`) read at startup. |
| **LiteLLM** | Library that speaks many LLM providers with a similar Python API. |
| **Strands** | Agent framework: model + tools in a loop, streaming events. |
| **SSRF** | “Server-Side Request Forgery”—tricking *your* server into hitting internal IPs; **`url_guard`** blocks obvious cases. |

---

## 3. Repository root

### `README.md`

Human-facing **quick start**: how to install Python deps, run Uvicorn, run Vite, where health checks live, pricing env vars. It is the first file collaborators open.

### `.gitignore`

Tells Git **not to commit** generated or secret files: virtualenv (`.venv/`), `node_modules/`, local SQLite `vecna.db`, `.env`, Python `__pycache__`, build output (`frontend/dist/`). **If `.env` were committed, your API keys would leak**—this file helps prevent that.

### `docs/PROJECT_GUIDE.md`

Shorter conceptual guide: stack glossary, dependency tables, folder map.

### `docs/FILE_BY_FILE.md`

This file—deep per-file notes.

---

## 4. Backend — `backend/`

### `requirements.txt`

**Pin / list Python libraries** to install with `pip install -r requirements.txt`. Each line is a **distribution name** (sometimes with a minimum version). See `PROJECT_GUIDE.md` for a column-by-column explanation. Nothing runs just because it is listed—you must `pip install`.

### `.env` (you create; not in Git)

**Secrets and local config**: API keys, model id, CORS, pricing. Copied from **`.env.example`**. The backend loads it via `python-dotenv` + Pydantic (`config.py`). Never commit real keys.

### `.env.example`

**Template without secrets**: safe to commit. Shows variable names and comments so someone new knows what to fill in.

### `vecna.db` (created at runtime; gitignored)

**SQLite database file** on disk. Tables `operations` and `billing_sessions` are created/migrated when the app starts. You can inspect it with [DB Browser for SQLite](https://sqlitebrowser.org/) or `sqlite3` CLI—useful when learning what columns exist.

---

## 5. Backend application package — `backend/app/`

### `app/__init__.py`

Often empty; marks `app` as a **Python package** so you can `from app.config import Settings`.

### `app/main.py` (large—this is the “center” of the HTTP server)

**FastAPI application** definition and almost all HTTP/WebSocket routes.

- **`lifespan`**: On startup, creates DB tables and runs **`schema_migrate`**; on shutdown, disposes the DB engine.
- **`Settings`**: Loaded once at import time from **`config`**.
- **CORS middleware**: Browsers block cross-origin requests unless the server allows your frontend origin—`CORS_ORIGINS` from settings lists allowed origins.
- **Pydantic models** (`StartOperationBody`, `OperationSummary`, …): Validate JSON bodies and document the API shape.
- **Routes (high level)**:
  - `POST /api/billing/sessions` — create a billing session id (stored client-side).
  - `GET /api/billing/sessions/{id}` — read cumulative session totals.
  - `POST /api/operations` — validate URL, create `Operation`, **spawn background task** `execute_operation`, return id. Uses **`ops_rate_limit`** from `rate_limit`.
  - `POST /api/operations/{id}/cancel` — **`cancel_task`** in runner.
  - `GET /api/config` — non-secret hints for the UI (model id, pricing visibility).
  - `GET /api/operations` — paginated list for history.
  - `GET /api/operations/{id}` — full detail for one operation (events, findings, costs).
  - `GET /api/operations/{id}/report.json` — **`build_structured_report`** export.
  - **`WebSocket /ws/operations/{id}`** — replay + live stream via **`hub`**.
  - `POST /api/demo/force-error` — demo of retry/error UI without spending tokens.
  - `GET /api/health/deep` — diagnostics (DB, keys, config warnings).
  - `GET /api/metrics` — Prometheus-style text metrics.
  - `GET /health` — minimal liveness check.

**Study tip:** When you add a feature, ask “is it HTTP (REST) or streaming (WebSocket)?”—that tells you which section of `main.py` to extend.

### `app/config.py`

**Central configuration object** (`Settings`):

- Loads **`.env`** from the `backend/` directory even if you start Uvicorn from elsewhere (path logic at top).
- **`load_dotenv(..., override=True)`** so values in `.env` win over empty shell exports.
- **Imports `agent.vecna_litellm` early** so LiteLLM is patched before other code imports `litellm`.
- Fields map to **environment variables** (e.g. `VECNA_HIDE_COSTS` → `vecna_hide_costs`).
- **`model_validator export_keys_to_environ`**: Copies keys into `os.environ` because LiteLLM and Strands sometimes read keys from the environment directly (especially OpenRouter + OpenAI-compatible paths).

**Why it matters:** One place to see “what can be configured” without reading the whole codebase.

### `app/database.py`

**Minimal SQLAlchemy async setup:**

- **`Base`**: Declarative base class; models inherit from it.
- **`make_engine(settings)`**: Creates `create_async_engine(settings.database_url)` — default URL uses **`aiosqlite`** driver + local file `vecna.db`.
- **`make_session_factory`**: Returns a factory that produces **`AsyncSession`** objects for `async with session_factory() as session:`.

**Student note:** “Engine” ≈ connection pool; “session” ≈ one transaction-ish scope for queries.

### `app/models.py`

**SQLAlchemy ORM models** (tables):

- **`BillingSession`**: One row per “operator session” (browser session id)—stores **rolled-up** token totals, costs, scan/tool fees across many operations.
- **`Operation`**: One row per scan: URL, status (`pending`/`running`/`completed`/`failed`/…), token counts, JSON blobs for **`events_json`** (stream log) and **`findings_json`**, foreign key **`session_id`** to billing.

Column names align with what the **frontend** and **`runner`** expect—changing them requires migrations + UI updates.

### `app/schema_migrate.py`

SQLite’s **`create_all`** does not add columns to **existing** tables. This file runs **`ALTER TABLE ... ADD COLUMN`** for columns added after the first deploy. Called from **`lifespan`** in `main.py`.

**Lesson:** In production you might use Alembic; here the project keeps migrations tiny and explicit.

### `app/url_guard.py`

**Safety gate** for user-supplied URLs: rejects loopback, private IP ranges, metadata hostnames, etc., to reduce **SSRF** risk when your server fetches those URLs during recon.

Returns `(True, "")` or `(False, reason_string)`; `main.py` turns failures into HTTP 400.

### `app/hub.py`

**In-memory pub/sub** for live operations:

- **`OperationEventHub`** holds `operation_id → list of asyncio.Queue`.
- **`register`**: Subscriber (WebSocket handler) gets a queue; **`publish`** pushes a dict to every queue for that operation id.
- **`unregister`**: WebSocket disconnect cleanup.

**Why queues?** Multiple browser tabs could subscribe; each gets its own queue. **Nothing is persisted here**—persistence is `Operation.events_json` in the DB.

---

## 6. Backend services — `backend/app/services/`

### `services/__init__.py`

Package marker + short docstring; may re-export nothing—just makes `app.services.runner` importable.

### `services/runner.py` (long—heart of “run one scan”)

**`execute_operation`** orchestrates a single operation:

1. Sets **`set_findings_bucket`** so tools can append findings.
2. Marks operation **`running`**, logs LLM query telemetry.
3. **`build_agent`**, **`attach_operation_runtime`** (context for tool threads).
4. **`async for raw in agent.stream_async(prompt)`**: Consumes Strands stream; **`event_to_jsonable`** sanitizes; **`hub.publish`** sends to WebSockets; **`_persist`** periodically writes DB.
5. On **`AgentResult`**, merges usage metrics, publishes credit update, findings delta.
6. **Retry loop**: On retryable errors, **`classify_agent_error`**, sleep with backoff, rebuild agent, retry (capped).
7. **Success path**: **`billable_for_completed_scan`**, **`record_operation_for_session`**, final **`_persist`**, **`hub.publish` done**.
8. **Cancel path**: `asyncio.CancelledError` from **`cancel_task`**.

Also **`register_task` / `cancel_task` / `_deregister_task`** for cancellation.

**Study tip:** Trace a single event from `hub.publish` to `App.tsx` WebSocket handler—you’ll understand streaming.

### `services/http_client.py`

**Outbound HTTP for recon tools** (not the LLM):

- Timeouts, User-Agent, per-request id header, structured logging.
- **`detect_gateway_from_model_id`**: Maps LiteLLM model id prefix (`openrouter/`, …) to a short label for telemetry (LLM gateway, separate from tool HTTP).
- **`recon_client()`**: Context manager yielding an `httpx` client—tools should use this instead of raw `httpx.get` so behavior is consistent.

### `services/cost.py`

**Token → USD** estimates:

- Static **`_PREFIX_COSTS`** table for models LiteLLM might not price.
- **`usage_from_metrics`**: Converts Strands **`EventLoopMetrics`** into **`UsageSummary`**.
- **`calculate_cost_from_tokens`**, **`format_cost`**, etc.

**Separation:** `cost.py` is **LLM money**; `pricing.py` adds **platform** scan/tool fees.

### `services/pricing.py`

**Platform billing on top of token cost:**

- Default **tool family** map: DNS / network / paths tools.
- **`BillableBreakdown`**, **`billable_for_completed_scan`**, **`compute_tool_fees`** (legacy flat vs tiered JSON).
- **`tool_pricing_public_snapshot`** for **`GET /api/config`** (no secrets).

### `services/billing.py`

**Billing session persistence**: create session row, **`record_operation_for_session`** after a successful op, **`get_session_snapshot`** for API responses. Aggregates per-model usage JSON.

### `services/telemetry.py`

**Structured logging** (`logger.info` with JSON) for operations, tool lifecycle, LLM retries, unknown model cost—helps operators grep logs or ship to a log aggregator.

### `services/errors.py`

**Exception classification** for LLM/agent failures: maps exceptions to **`error_type`** strings (`rate_limit`, `timeout`, …), decides **retryability** and **user-facing messages**. Used by **`runner`** retry loop.

### `services/operation_context.py`

**Context variables** (`contextvars`) so sync tool code running on a worker thread still knows:

- Current **`operation_id`**
- **`events_log`**
- Main **`asyncio` loop** and **`session_factory`**

**`schedule_tool_emit`**: Thread-safe way to push “tool started/finished” events to **`hub`** + DB from tools.

**Why:** Strands runs tools in threads; normal global variables would be unsafe—`contextvars` propagate per-async-task.

### `services/rate_limit.py`

**In-memory per-IP limits** for `POST /api/operations` (sliding windows). Env **`VECNA_OPS_PER_MINUTE`**, **`VECNA_OPS_PER_HOUR`**. Raises HTTP 429 when exceeded.

---

## 7. Backend lib — `backend/app/lib/`

### `lib/serialize.py`

Converts raw Strands stream dicts into **JSON-safe** dicts and adds **`human_line`**—a **single readable sentence** for the UI timeline. Drops huge/noisy keys, handles **`AgentResult`**, parses nested “reasoning” / “tool use” chunks.

**Coupling:** Frontend **`streamMerge.ts`** assumes certain `human_line` patterns—change carefully.

### `lib/report.py`

**`build_structured_report(op)`** builds the JSON for **`GET .../report.json`**: grouped findings, telemetry block, schema version string.

---

## 8. Backend agent — `backend/agent/`

### `agent/__init__.py`

Package marker.

### `agent/vecna_agent.py`

**Wires the Strands `Agent`:**

- **`SYSTEM_PROMPT`**: Instructions (passive recon, which tools, output format).
- **Key helpers** `_openrouter_key`, `_openai_key`, `_claude_direct_api_key`, … — read settings + env.
- **`_client_args_for_model`**: Returns provider-specific kwargs for LiteLLM (e.g. OpenRouter base URL).
- **`build_agent(settings)`**: Validates keys for the chosen model prefix, constructs **`VecnaLiteLLMModel`**, registers tools from **`tools.py`**, returns **`Agent`**.
- **`user_message_for_target(url)`**: User message string passed to the agent.

**Error messages** here are what developers see if keys are missing.

### `agent/vecna_litellm.py`

**Monkey-patches LiteLLM’s `completion` / `acompletion`** so `openrouter/` models always get API key + base URL (workaround for Strands/LiteLLM edge cases). Also defines **`VecnaLiteLLMModel`** subclass that merges **`client_args`** into each request so auth wins over defaults.

Imported **side-effect first** from `config.py`—unusual pattern, but ensures patches apply before any `litellm` call.

### `agent/tools.py`

**Three `@tool` functions** the LLM can invoke:

1. **`resolve_dns_and_subdomains`** — DNS + crt.sh passive discovery.
2. **`analyze_http_security_headers`** — GET + header analysis.
3. **`probe_common_paths`** — fixed path list GETs.

Implementation functions **`_resolve_dns_impl`**, etc., call **`run_tool_with_hooks`** from **`tool_hooks`**. Uses **`recon_client`**, **`set_findings_bucket`** / **`_merge_findings`** for the findings panel.

### `agent/tool_hooks.py`

**`run_tool_with_hooks`**: Before/after wrapper—emits lifecycle events, telemetry, timings. Keeps **`tools.py`** focused on recon logic.

---

## 9. Backend tests — `backend/tests/`

### `conftest.py`

Pytest **fixture bootstrap**: sets **`DATABASE_URL`** to **in-memory SQLite** so tests do not touch your real `vecna.db`.

### `test_api.py`, `test_billing.py`, `test_cost.py`, `test_pricing.py`, `test_errors.py`, `test_http_client.py`, `test_tool_hooks.py`

Each file **targets one area**: HTTP routes, billing math, cost estimates, pricing rules, error classification, HTTP client gateway detection, tool hook behavior.

**How to learn:** Read a test name + assertion—it documents expected behavior often better than comments.

---

## 10. Frontend — `frontend/`

### `package.json`

**npm manifest**: scripts (`dev`, `build`, `lint`), dependencies (React, Vite, Tailwind, Radix, …). **`npm install`** reads this and writes **`package-lock.json`** (exact resolved versions).

### `package-lock.json`

Lockfile—commit it so everyone gets the **same** dependency tree.

### `vite.config.ts`

**Vite configuration**:

- Plugins: React, Tailwind v4.
- **`@` alias** → `src/`.
- **Dev server proxy**: `/api` and `/ws` → `127.0.0.1:8000` so the browser uses **same origin** (no CORS issues in dev).

### `tsconfig.json`, `tsconfig.app.json`, `tsconfig.node.json`

**TypeScript compiler options** split for app vs Vite config files—standard Vite + React setup.

### `index.html`

Single HTML shell: **`div#root`** where React mounts; script entry **`/src/main.tsx`**.

### `src/main.tsx`

**React entrypoint**: imports global CSS, renders **`<App />`** inside **`StrictMode`** (extra dev checks).

### `src/App.tsx` (very large)

**The entire UI** in one component (common in small apps):

- State for URL input, operation id, WebSocket messages, findings, history list, billing session id in **`localStorage`** (`BILLING_SESSION_KEY`).
- Fetches **`GET /api/config`**, **`POST /api/operations`**, opens WebSocket, handles message types (`stream`, `done`, `retry`, …).
- Uses **`appendStreamEntry` / `foldStreamEntries`** from **`streamMerge.ts`** to build a readable timeline.
- UI components from **`components/ui/*`**.

**Study tip:** Search for `WebSocket` and walk through `onmessage`—that is the live feed.

### `src/App.css` / `src/index.css`

**Styles**—Tailwind layers + any custom rules for layout/branding.

### `src/lib/utils.ts`

Tiny helpers—typically **`cn()`** merges class names (Tailwind-friendly).

### `src/lib/streamMerge.ts`

**Merges noisy stream lines** for display: combines chunked tool args and text deltas, collapses duplicate `human_line`s, handles “cumulative vs incremental” provider quirks. Documented in file header comments.

### `src/components/ui/*.tsx`

**Reusable UI primitives** (Button, Card, Input, ScrollArea, Badge) built with **Radix** primitives + Tailwind. Shadcn-style pattern—consistent look, accessible focus/keyboard behavior.

### `public/favicon.svg`, `public/icons.svg`

Static assets served as-is; `index.html` references favicon.

---

## 11. Electron — `electron/` (optional)

### `package.json`

Declares **Electron** devDependency and **`npm start`** script.

### `main.js`

Minimal **desktop shell**: opens a **`BrowserWindow`** and loads **`http://localhost:5173`**. You must still run Vite + backend separately unless you change this.

**Learning note:** This is **not** a production packaging pipeline—just a window around the dev URL.

---

## 12. How to practice (concrete exercises)

1. **Trace a request:** From `App.tsx` `fetch('/api/operations')` → `main.py` `start_operation` → `runner.execute_operation` → first `hub.publish`.
2. **Change copy only:** Edit `SYSTEM_PROMPT` in `vecna_agent.py`, rerun a scan, observe different report tone.
3. **Add a log line:** In `telemetry.py`, add one `logger.info` in a function you understand; watch the Uvicorn console.
4. **Run one test:** `cd backend && pytest tests/test_http_client.py -q` (or any `test_*.py`) and read that file alongside the module it imports.

---

## 13. What this guide does *not* list

- **`node_modules/`**, **`.venv/`** — third-party code; use docs sites, not line-by-line reading.
- **`frontend/dist/`** — build output; regenerated by `npm run build`.
- **`.git/`** — Git internals; learn Git separately.

If you add new first-party files, append a short subsection here so the next reader (or future you) stays oriented.
