# How Vecna Operations Console works: an architecture essay

This document explains **what** the application is for, **how** the major pieces fit together, and **why** the design looks the way it does. It is meant to be read start to finish like an essay, with pointers to deeper references at the end.

---

## What it is

**Vecna Operations Console** is a small full-stack product for **passive security reconnaissance**: an operator supplies a **public HTTPS URL**, and the system runs an **AI agent** that can call a fixed set of **tools** (DNS and certificate transparency names, HTTP security headers, a bounded list of common paths). The agent then produces a **structured report** in natural language. Nothing here performs exploitation or aggressive scanning; the tools are deliberately narrow and read-only in spirit.

Technically, the stack is **React + TypeScript** in the browser, **FastAPI** on the server, **SQLite** for persistence, and **Strands** plus **LiteLLM** to drive whichever cloud LLM you configure. That choice trades enterprise complexity for clarity: one process can run the API, one database file can hold state, and a single-page UI can show a live timeline without a separate message broker.

---

## The operator’s journey

From the user’s perspective, the flow is linear. They open the web UI, optionally start or attach a **billing session** (so multiple scans can roll up into one session total), enter a target URL, and submit. The server **immediately** returns an **operation id**; the heavy work runs **asynchronously** so the HTTP request does not wait for the LLM to finish.

The UI opens a **WebSocket** to that operation and receives a stream of events: model output, tool start and end lines, findings, token and cost updates, and finally **done** or **error**. If they open history, they see past operations from the same server. If they download **report.json**, they get a stable JSON export of findings and telemetry for that run.

That journey is implemented as three cooperating rhythms: **HTTP** for commands and queries, **WebSocket** for push updates, and **background tasks** for the long-running agent.

---

## Server shape: API, tasks, and data

The **FastAPI** application in `main.py` is the public surface. It configures **CORS** from environment variables, wires **REST routes** for operations and billing sessions, exposes **health** and **diagnostics**, optional **Prometheus-style metrics** text, and a **WebSocket** route per operation id. It does not embed business logic for token math or retries; that lives under `app/services/`.

When a new operation is created, the handler validates the URL against **`url_guard`**, which rejects obvious **Server-Side Request Forgery (SSRF)**-style targets (loopback, private ranges, metadata hostnames). It inserts a row into **SQLite** via SQLAlchemy models (`Operation`), then schedules **`execute_operation`** as an **asyncio** task. The HTTP response returns only the id; the task owns the rest of the lifecycle.

**SQLite** stores operations and billing sessions: URLs, statuses, JSON blobs for streamed events and findings, token counts, cost estimates, and platform fee breakdowns. The schema can evolve through small **additive migrations** in `schema_migrate.py` because SQLite does not automatically add columns to existing tables the way some hosted databases do.

---

## The agent loop: Strands, LiteLLM, and tools

The **runner** (`execute_operation` in `services/runner.py`) builds a **Strands `Agent`** from `vecna_agent.py`. The agent combines a **system prompt** (how to behave, which tools exist, output format) with a **user message** derived from the target URL. The model is reached through **LiteLLM**, which abstracts many providers behind one calling convention; configuration passes **API keys** and **model ids** from `Settings` and `.env`.

Because Strands and LiteLLM sometimes merge request parameters in an order that lets inner fields **override** client authentication, the project includes **`VecnaLiteLLMModel`**: a thin subclass that merges request dicts so **`client_args` win**, and a **carefully ordered import** of `vecna_litellm.py` that **monkey-patches** LiteLLM’s completion entrypoints for **OpenRouter**-style models so keys and base URLs are not dropped on certain code paths. That is integration glue, not core domain logic, but it is essential for reliable auth in practice.

The **tools** are ordinary Python functions decorated with Strands’ `@tool`. They return **strings** (often JSON) that become **tool results** in the model’s conversation. Between tool calls, the model’s **context** is whatever Strands maintains: the transcript of user message, assistant turns, tool calls, and tool outputs. Separately, the application maintains its own **event log** for the UI; those two are related but not identical.

---

## Findings, hooks, and context variables

**Findings** are structured dicts (title, severity, category) that power the findings panel. They are collected in a **single Python list** per operation, registered through a **`ContextVar`** so that when tools run on **worker threads**, they still append to the **same list** the runner created. **Tool hooks** wrap each tool to emit **start** and **end** lifecycle events, timings, and telemetry without duplicating that boilerplate in every tool implementation.

**Operation context** (`operation_context.py`) uses additional **ContextVars** to carry the **operation id**, the **events log**, the running **asyncio loop**, and a **database session factory**. That lets synchronous tool code **schedule** asynchronous work back onto the main loop: publish to the hub, append to the persisted event list, and commit when appropriate. Without that bridge, tool threads could not safely talk to **async** FastAPI and **async** SQLAlchemy.

---

## The hub: fan-out without a message broker

**`hub.py`** implements a tiny **in-memory pub/sub**. Each **subscriber** (typically one WebSocket connection) registers an **`asyncio.Queue`** for a given **operation id**. When the runner or tool layer calls **`publish`**, every queue for that id receives a copy of the message. Multiple browser tabs watching the same run each get their own queue; all see the same stream.

The hub is **not durable**. If the process crashes, memory queues vanish. Durability comes from **persisting** `events_json` and `findings_json` on the `Operation` row as the run progresses, and from the WebSocket handler **replaying** stored events when a client connects to an operation that already has history. That pattern gives **resilience on reconnect** without requiring Redis.

---

## Money, visibility, and sessions

**Token cost** is estimated in `cost.py` using LiteLLM when possible and **static tables** when not, with a flag when the model is unknown. **Platform pricing** (`pricing.py`) layers optional **scan fees** and **tool surcharges** (flat or tiered by tool family) on top of LLM dollars. **Billing sessions** aggregate totals across many operations for a client-held **session id** stored in **localStorage**.

**`VECNA_HIDE_COSTS`** can hide dollar amounts in the UI while still computing them server-side—useful for demos or operators who should not see raw spend. None of this replaces your cloud provider’s billing; it is **application-level** accounting and UX.

**If you add real authenticated users later**, you should revisit this layer: today **`BillingSession`** is keyed by an id the **browser** holds (`localStorage`), not by a verified user identity. With login, you would typically **tie rollups to `user_id`** (or org), issue or bind **session ids server-side**, enforce **authorization** on `GET /api/billing/sessions/{id}`, and treat cost display as **per-tenant** policy—not only swap the storage key, but ensure one user cannot read another’s session totals.

---

## Errors, retries, and limits

**Transient** LLM and network failures are **classified** (`errors.py`) into stable categories (timeout, rate limit, overload, auth, and so on). **Retryable** categories trigger **backoff** and rebuild the agent in a loop with a cap; the UI receives **retry** frames so the operator sees that work is still happening. **Non-retryable** errors (bad auth, invalid request shape) stop the run with a **clean user-facing message**.

Separately, **HTTP rate limiting** on **starting** new operations is enforced per client IP in process memory with sliding windows. That limits **abuse of your API surface**; it does not replace the LLM provider’s own quotas. For multi-instance deployments, this limiter would need a **shared** store to behave consistently.

---

## Frontend: one app, two transports

The **React** application uses **`fetch`** for REST and a **WebSocket** for the live feed. In development, **Vite** proxies `/api` and `/ws` to the backend so the browser treats everything as one origin. For **Electron** or a built `file://` load, **`VITE_API_ORIGIN`** points API calls at the backend explicitly.

The timeline merges noisy stream chunks in **`streamMerge.ts`** so operators see readable lines instead of duplicated fragments. State for operations, findings, costs, and reconnect logic lives largely in **`App.tsx`**, which is intentionally monolithic for a project of this size.

---

## Observability and operations

**Structured JSON logs** (`telemetry.py`) complement human-readable server logs. **`/api/health/deep`** aggregates checks: database, model id versus API keys, CORS warnings, configured rate limits. **`/api/metrics`** exposes **Prometheus-style** text for optional scraping by monitoring systems you may run later.

---

## Design tradeoffs this stack accepts

The architecture **optimizes for clarity and a single deployable unit**, not for multi-tenant SaaS at scale. There is **no built-in authentication** on the API; **SSRF** protection is **best-effort** rather than a full zero-trust egress design; **rate limits** are **per process**; **SQLite** serializes concurrent writes more than Postgres would. **Billing sessions** are anonymous-browser–scoped until you integrate auth and re-key sessions to real accounts (see **Money, visibility, and sessions** above). Those are acceptable for local and small-team use and become the first things to harden for internet-facing production.

---

## Closing

Vecna Ops is best understood as **three stories told in parallel**: the **LLM conversation** (Strands and tool results), the **application diary** (events and findings persisted for the UI and exports), and the **business overlay** (costs, fees, sessions, visibility). The **hub** ties live viewers to the runner; **context variables** tie synchronous tools to async infrastructure; **FastAPI** ties HTTP and WebSockets to SQLite. If you need a map from feature names to files, read **`CAPABILITIES.md`**. If you need a file-by-file tour, read **`FILE_BY_FILE.md`**. If you need vocabulary and package lists, read **`PROJECT_GUIDE.md`**.
