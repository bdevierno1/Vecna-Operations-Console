# How Vecna Ops implements core product capabilities

This document maps **common “console” style features** (cost visibility, retries, HTTP hygiene, telemetry, limits, streaming, history, health, WebSockets) to **concrete files and APIs in this repo**. It describes only what exists here—no external reference trees.

---

## Cost & usage

| Idea | Where it lives |
|------|----------------|
| **Token → USD** (per model, cache buckets) | `backend/app/services/cost.py` — LiteLLM when available, else static prefix table, unknown-model fallback + flag. |
| **Session-level totals** (tokens, $, per-model usage) | `backend/app/services/billing.py` — `BillingSession` rows; `record_operation_for_session` after a completed run. |
| **Platform fees on top of LLM $** (scan fee, tool surcharges, tiered families) | `backend/app/services/pricing.py` + env vars in `backend/app/config.py` (`VECNA_SCAN_FEE_USD`, tiered JSON, etc.). |
| **“Who sees dollar amounts”** | `VECNA_HIDE_COSTS` in config → `GET /api/config` exposes `billing_visible`; frontend hides $ when off. |
| **Persist / restore billing session** | `POST /api/billing/sessions`, `GET /api/billing/sessions/{id}`; client stores `session_id` (e.g. `localStorage`) and sends it with `POST /api/operations`. |

---

## Errors & retries

| Idea | Where it lives |
|------|----------------|
| **Classify exceptions** (timeout, rate limit, auth, overload, …) | `backend/app/services/errors.py` — `classify_agent_error`. |
| **User-facing messages** (no raw stack traces in the UI path) | `errors.py` — `user_message_for_error`; runner publishes `type: "error"` on WebSocket. |
| **Retry policy** (which types retry, backoff + jitter, `Retry-After`) | `errors.py` — `is_retryable`, `retry_delay_seconds`; `backend/app/services/runner.py` — loop with sleep, rebuild agent, cap on attempts. |
| **HTTP 429 for rate-limited *API* usage** | `rate_limit.py` (separate from LLM provider 429s). |

---

## API client hygiene (outbound HTTP)

| Idea | Where it lives |
|------|----------------|
| **Single place for recon fetches** (timeouts, UA, request id, structured logs) | `backend/app/services/http_client.py` — `recon_client()`; tools use it instead of ad hoc `httpx`. |
| **Gateway hint from model id** (telemetry label for LLM provider) | `http_client.py` — `detect_gateway_from_model_id` (not the same as tool HTTP). |

---

## Observability

| Idea | Where it lives |
|------|----------------|
| **Structured JSON log lines** (operations, retries, tool lifecycle, LLM query) | `backend/app/services/telemetry.py` — `log_event` + helpers; grep-friendly `logger.info` with JSON payload. |
| **Aggregated metrics text** (Prometheus-style) | `GET /api/metrics` in `backend/app/main.py` — sums from `Operation` rows. |

---

## Rate limits & quotas

| Idea | Where it lives |
|------|----------------|
| **Per-IP limits on starting scans** (clear message, not a raw stack) | `backend/app/services/rate_limit.py` — sliding windows, `VECNA_OPS_PER_MINUTE` / `VECNA_OPS_PER_HOUR`; `Depends(ops_rate_limit)` on `POST /api/operations`. |
| **Expose limits to operators** | `current_limits()` used by `GET /api/health/deep`. |

---

## Streaming & tools

| Idea | Where it lives |
|------|----------------|
| **LLM + agent stream** | `backend/app/services/runner.py` — `async for raw in agent.stream_async(...)`; events serialized in `backend/app/lib/serialize.py`. |
| **Cancel an in-flight run** | `POST /api/operations/{id}/cancel` → `cancel_task` in `runner.py`; `asyncio.CancelledError` path persists `cancelled` status. |
| **Tool start / end (duration, ok/error)** | `backend/agent/tool_hooks.py` — `run_tool_with_hooks`; `backend/app/services/operation_context.py` — `schedule_tool_emit` → hub + `events_log`. |
| **Findings panel (structured items from tools)** | `backend/agent/tools.py` — `_merge_findings` into shared list; runner publishes `type: "findings"` when the list grows. |
| **Fan-out live events** | `backend/app/hub.py` — per-operation queues; WebSocket handler in `main.py` drains a queue. |

---

## Session & history UX

| Idea | Where it lives |
|------|----------------|
| **List past operations** | `GET /api/operations` — cursor `before=`, newest first. |
| **Detail for one operation** | `GET /api/operations/{id}` — status, tokens, costs, `events_json`, `findings_json`. |
| **Billing session rollup** | `BillingSession` + `session_id` on `Operation`; UI can show session totals when linked. |
| **Resume / deep link** (pattern: select op by id from URL/query) | `frontend/src/App.tsx` — reads `?op=` (and related) to focus an operation after load. |
| **Structured export** | `GET /api/operations/{id}/report.json` — `backend/app/lib/report.py`. |

---

## Health & diagnostics

| Idea | Where it lives |
|------|----------------|
| **One endpoint for “is this deploy OK?”** | `GET /api/health/deep` in `main.py` — DB reachability, model id vs API keys, CORS warnings, rate limit snapshot. |
| **Minimal liveness** | `GET /health` — `{ "status": "ok" }`. |

---

## WebSocket & reconnection

| Idea | Where it lives |
|------|----------------|
| **Live timeline + findings + done/error** | `WebSocket /ws/operations/{id}` in `main.py` — replay from DB if reconnecting mid-run; then subscribe via `hub`. |
| **Client reconnect with backoff** | `frontend/src/App.tsx` — `onclose` schedules `connectWs` with exponential backoff; `onopen` clears “reconnecting” state; ping interval to keep connection warm. |
| **Dev proxy** | `frontend/vite.config.ts` — `/api` and `/ws` to backend. |

---

## Quick file index (by theme)

| Theme | Primary files |
|-------|-----------------|
| Cost / bill / price | `cost.py`, `pricing.py`, `billing.py`, `config.py` |
| Errors / retry | `errors.py`, `runner.py` |
| HTTP client | `http_client.py` |
| Telemetry / metrics | `telemetry.py`, `main.py` (`/api/metrics`) |
| Rate limit | `rate_limit.py` |
| Agent + tools | `agent/tools.py`, `tool_hooks.py`, `vecna_agent.py`, `runner.py` |
| Live events | `hub.py`, `operation_context.py`, `serialize.py`, `main.py` (WS) |
| UI | `frontend/src/App.tsx`, `frontend/src/lib/streamMerge.ts`, `frontend/src/lib/apiBase.ts` |

---

## Related docs

- **[PROJECT_GUIDE.md](PROJECT_GUIDE.md)** — stack vocabulary and package list.  
- **[FILE_BY_FILE.md](FILE_BY_FILE.md)** — per-file walkthrough.
