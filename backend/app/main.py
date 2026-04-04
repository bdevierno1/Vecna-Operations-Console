from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import PlainTextResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import Settings  # loads agent.vecna_litellm (patches litellm for OpenRouter)

from app.database import Base, make_engine, make_session_factory
from app.hub import hub
from app.lib.report import build_structured_report
from app.models import Operation
from app.services.billing import create_billing_session, get_session_snapshot
from app.services.cost import get_model_pricing_string
from app.services.pricing import display_billable_total, tool_pricing_public_snapshot
from app.services.rate_limit import current_limits, ops_rate_limit
from app.services.runner import cancel_task, execute_operation, register_task
from app.schema_migrate import _sqlite_add_missing_columns
from app.url_guard import is_safe_public_target

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

settings = Settings()
logger.info(
    "LLM config: model_id=%r openrouter_key=%s openai_key=%s",
    settings.litellm_model_id,
    bool(settings.openrouter_api_key or os.environ.get("OPENROUTER_API_KEY")),
    bool(settings.openai_api_key or os.environ.get("OPENAI_API_KEY")),
)
if settings.litellm_model_id.startswith("openai/") and not (
    settings.openai_api_key or os.environ.get("OPENAI_API_KEY")
):
    logger.warning(
        "LITELLM_MODEL_ID is OpenAI (%s) but OPENAI_API_KEY is not set. "
        "For OpenRouter + Qwen, set LITELLM_MODEL_ID=openrouter/... and OPENROUTER_API_KEY.",
        settings.litellm_model_id,
    )
if settings.litellm_model_id.startswith("openrouter/") and not (
    settings.openrouter_api_key or os.environ.get("OPENROUTER_API_KEY")
):
    logger.warning(
        "LITELLM_MODEL_ID is OpenRouter (%s) but OPENROUTER_API_KEY is not set.",
        settings.litellm_model_id,
    )

engine = make_engine(settings)
session_factory = make_session_factory(engine)


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_sqlite_add_missing_columns)
    yield
    await engine.dispose()


app = FastAPI(title="Vecna Operations Console", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class StartOperationBody(BaseModel):
    target_url: str = Field(..., min_length=4, description="https://example.com")
    session_id: str | None = Field(
        default=None,
        description="Billing session id (from POST /api/billing/sessions); accumulates cost across operations.",
    )


class BillingSessionResponse(BaseModel):
    session_id: str
    total_cost_usd: float = 0.0  # LLM/token $
    total_scan_fees_usd: float = 0.0
    total_tool_fees_usd: float = 0.0
    total_tool_units: int = 0
    total_billable_usd: float = 0.0
    total_credits: float = 0.0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cache_read_tokens: int = 0
    total_cache_write_tokens: int = 0
    operation_count: int = 0
    wall_time_seconds: float = 0.0
    model_usage: dict = Field(default_factory=dict)


class OperationSummary(BaseModel):
    id: str
    target_url: str
    status: str
    credits_used: float
    cost_usd: float = 0.0
    billable_total_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    duration_seconds: float | None = None
    created_at: str
    error_message: str | None = None
    session_id: str | None = None


@app.post("/api/billing/sessions", response_model=dict[str, str])
async def create_session():
    """Start a billing session (persisted). Client stores id (e.g. localStorage) and sends it with each operation."""
    sid = str(uuid.uuid4())
    await create_billing_session(session_factory, sid)
    return {"session_id": sid}


@app.get("/api/billing/sessions/{session_id}", response_model=BillingSessionResponse)
async def get_billing_session(session_id: str):
    snap = await get_session_snapshot(session_factory, session_id)
    if not snap:
        raise HTTPException(404, detail="Unknown billing session")
    d = snap.to_api_dict()
    return BillingSessionResponse(**d)


@app.post("/api/operations", response_model=dict[str, str], dependencies=[Depends(ops_rate_limit)])
async def start_operation(body: StartOperationBody):
    ok, reason = is_safe_public_target(body.target_url)
    if not ok:
        raise HTTPException(400, detail=reason or "URL not allowed")

    sid = (body.session_id or "").strip() or None
    if sid:
        await create_billing_session(session_factory, sid)

    op = Operation(target_url=body.target_url.strip(), status="pending", session_id=sid)
    async with session_factory() as session:
        session.add(op)
        await session.commit()
        await session.refresh(op)
        op_id = op.id

    task = asyncio.create_task(
        execute_operation(
            session_factory,
            settings,
            op_id,
            body.target_url.strip(),
            billing_session_id=sid,
        )
    )
    register_task(op_id, task)
    return {"id": op_id}


@app.post("/api/operations/{operation_id}/cancel", response_model=dict[str, str])
async def cancel_operation(operation_id: str):
    """Interrupt an in-progress operation."""
    async with session_factory() as session:
        op = await session.get(Operation, operation_id)
        if not op:
            raise HTTPException(404, detail="Not found")
        if op.status not in ("pending", "running"):
            raise HTTPException(409, detail=f"Operation is already {op.status}")

    found = cancel_task(operation_id)
    if not found:
        # Task may have already finished between our check and the cancel
        raise HTTPException(409, detail="Operation is not currently running")
    return {"status": "cancelling"}


@app.get("/api/config")
async def public_config():
    """Frontend: model id, static pricing hint, billing UI visibility toggle."""
    mid = settings.litellm_model_id.strip()
    return {
        "litellm_model_id": mid,
        "model_pricing_display": get_model_pricing_string(mid),
        "billing_visible": not settings.vecna_hide_costs,
        "vecna_scan_fee_usd": settings.vecna_scan_fee_usd,
        "vecna_tool_unit_fee_usd": settings.vecna_tool_unit_fee_usd,
        "tool_pricing": tool_pricing_public_snapshot(settings),
    }


@app.get("/api/operations", response_model=list[OperationSummary])
async def list_operations(limit: int = 50, before: str | None = None):
    """List operations newest-first. Cursor-based pagination via `before=<operation_id>`."""
    limit = max(1, min(limit, 200))
    async with session_factory() as session:
        q = select(Operation).order_by(Operation.created_at.desc())
        if before:
            pivot = await session.get(Operation, before)
            if pivot and pivot.created_at:
                q = q.where(Operation.created_at < pivot.created_at)
        res = await session.execute(q.limit(limit))
        rows = res.scalars().all()
    out: list[OperationSummary] = []
    for r in rows:
        out.append(
            OperationSummary(
                id=r.id,
                target_url=r.target_url,
                status=r.status,
                credits_used=r.credits_used,
                cost_usd=r.cost_usd or 0.0,
                billable_total_usd=r.billable_total_usd or 0.0,
                input_tokens=r.input_tokens or 0,
                output_tokens=r.output_tokens or 0,
                duration_seconds=r.duration_seconds,
                created_at=r.created_at.isoformat() if r.created_at else "",
                error_message=r.error_message,
                session_id=r.session_id,
            )
        )
    return out


@app.get("/api/operations/{operation_id}")
async def get_operation(operation_id: str):
    async with session_factory() as session:
        op = await session.get(Operation, operation_id)
        if not op:
            raise HTTPException(404, detail="Not found")
        llm = op.cost_usd or 0.0
        scan = op.scan_fee_usd or 0.0
        tool_f = op.tool_fee_usd or 0.0
        stored_b = op.billable_total_usd or 0.0
        billable = display_billable_total(
            llm_cost_usd=llm,
            scan_fee_usd=scan,
            tool_fee_usd=tool_f,
            stored_billable_usd=stored_b,
        )
        return {
            "id": op.id,
            "target_url": op.target_url,
            "status": op.status,
            "credits_used": op.credits_used,
            "cost_usd": llm,
            "scan_fee_usd": scan,
            "tool_fee_usd": tool_f,
            "tool_units": op.tool_units or 0,
            "billable_total_usd": billable,
            "input_tokens": op.input_tokens or 0,
            "output_tokens": op.output_tokens or 0,
            "cache_read_tokens": op.cache_read_tokens or 0,
            "cache_write_tokens": op.cache_write_tokens or 0,
            "duration_seconds": op.duration_seconds,
            "cost_estimate_unknown": bool(op.cost_estimate_unknown),
            "summary_text": op.summary_text,
            "events_json": op.events_json or [],
            "findings_json": op.findings_json or [],
            "error_message": op.error_message,
            "created_at": op.created_at.isoformat() if op.created_at else None,
            "session_id": op.session_id,
            "pricing_breakdown": op.pricing_breakdown_json,
        }


@app.get("/api/operations/{operation_id}/report.json")
async def get_operation_report_json(operation_id: str):
    """Machine-readable structured report (findings by category, summary, telemetry)."""
    async with session_factory() as session:
        op = await session.get(Operation, operation_id)
        if not op:
            raise HTTPException(404, detail="Not found")
        report = build_structured_report(op)
        if op.session_id:
            snap = await get_session_snapshot(session_factory, op.session_id)
            if snap:
                report["session_billing"] = snap.to_api_dict()
        return report


@app.websocket("/ws/operations/{operation_id}")
async def operation_ws(websocket: WebSocket, operation_id: str):
    await websocket.accept()
    async with session_factory() as session:
        op = await session.get(Operation, operation_id)
        if not op:
            await websocket.send_json({"type": "error", "message": "Unknown operation"})
            await websocket.close()
            return
        if op.events_json:
            await websocket.send_json({"type": "replay", "events": op.events_json})
        if op.findings_json:
            await websocket.send_json({"type": "findings", "items": op.findings_json})
        if op.status == "completed":
            llm = op.cost_usd or 0.0
            scan = op.scan_fee_usd or 0.0
            tool_f = op.tool_fee_usd or 0.0
            stored_b = op.billable_total_usd or 0.0
            billable = display_billable_total(
                llm_cost_usd=llm,
                scan_fee_usd=scan,
                tool_fee_usd=tool_f,
                stored_billable_usd=stored_b,
            )
            stored_bd = op.pricing_breakdown_json if isinstance(op.pricing_breakdown_json, dict) else None
            if stored_bd:
                bd = dict(stored_bd)
                bd["billable_total_usd"] = billable
            else:
                bd = {
                    "llm_cost_usd": llm,
                    "scan_fee_usd": scan,
                    "tool_fee_usd": tool_f,
                    "tool_units": op.tool_units or 0,
                    "billable_total_usd": billable,
                }
            done_payload: dict = {
                "type": "done",
                "credits": op.credits_used,
                "cost_usd": llm,
                "billable_total_usd": billable,
                "billable_breakdown": bd,
                "input_tokens": op.input_tokens or 0,
                "output_tokens": op.output_tokens or 0,
                "duration_seconds": op.duration_seconds,
                "summary": (op.summary_text or "")[:20_000],
                "findings": op.findings_json or [],
            }
            if op.session_id:
                snap = await get_session_snapshot(session_factory, op.session_id)
                if snap:
                    done_payload["session_billing"] = snap.to_api_dict()
            done_payload["cost_estimate_unknown"] = bool(op.cost_estimate_unknown)
            await websocket.send_json(done_payload)
            await websocket.close()
            return
        if op.status == "failed":
            await websocket.send_json({"type": "error", "message": op.error_message or "failed"})
            await websocket.close()
            return

    q = hub.register(operation_id)
    try:
        while True:
            msg = await q.get()
            await websocket.send_json(msg)
            if msg.get("type") in ("done", "error"):
                break
    except WebSocketDisconnect:
        pass
    finally:
        hub.unregister(operation_id, q)


@app.post("/api/demo/force-error")
async def demo_force_error(error_type: str = "rate_limit"):
    """Demo only: creates an operation that streams realistic retry frames then fails.

    Useful for showing the error-classification + retry UI without burning tokens.
    error_type can be any string from app.services.errors (rate_limit, server_overload, timeout, auth_error, …).
    """
    from app.services.errors import _USER_MESSAGES, ET_UNKNOWN  # local import keeps demo dep isolated

    op = Operation(target_url="demo://force-error", status="running")
    async with session_factory() as session:
        session.add(op)
        await session.commit()
        await session.refresh(op)
        op_id = op.id

    async def _run(op_id: str, etype: str) -> None:
        retries = [
            (0.6, 1, etype,           "Retrying in 1s (attempt 1/7)…"),
            (1.2, 2, etype,           "Retrying in 2s (attempt 2/7)…"),
            (1.0, 3, "server_overload", "Retrying in 4s (attempt 3/7)…"),
        ]
        for delay, attempt, et, msg in retries:
            await asyncio.sleep(delay)
            await hub.publish(op_id, {
                "type": "retry",
                "attempt": attempt,
                "delay_seconds": delay,
                "error_type": et,
                "message": msg,
            })
        await asyncio.sleep(1.0)
        final_msg = _USER_MESSAGES.get(etype, _USER_MESSAGES[ET_UNKNOWN])
        await hub.publish(op_id, {
            "type": "error",
            "message": final_msg,
            "error_type": etype,
            "attempts": len(retries),
        })
        async with session_factory() as session:
            row = await session.get(Operation, op_id)
            if row:
                row.status = "failed"
                row.error_message = final_msg
                await session.commit()

    asyncio.create_task(_run(op_id, error_type))
    return {"id": op_id}


@app.get("/api/health/deep")
async def health_deep():
    """Diagnostic endpoint: DB, API keys vs model id, config warnings, rate limits."""
    _start = time.monotonic()
    checks: dict[str, dict] = {}
    overall = "ok"

    # --- DB connectivity --------------------------------------------------
    try:
        async with session_factory() as session:
            count_res = await session.execute(
                select(func.count()).select_from(Operation)
            )
            op_count = count_res.scalar() or 0
        checks["database"] = {"status": "ok", "operation_count": op_count}
    except Exception as e:
        checks["database"] = {"status": "error", "detail": str(e)[:200]}
        overall = "error"

    # --- Model / API key --------------------------------------------------
    mid = settings.litellm_model_id.strip()
    known_prefixes = ("openrouter/", "openai/", "anthropic/", "gemini/", "bedrock/", "vertex/", "azure/")
    has_prefix = any(mid.startswith(p) for p in known_prefixes)
    key_checks: dict[str, bool] = {}
    if mid.startswith("openrouter/"):
        key_checks["OPENROUTER_API_KEY"] = bool(
            settings.openrouter_api_key or os.environ.get("OPENROUTER_API_KEY")
        )
    elif mid.startswith("openai/"):
        key_checks["OPENAI_API_KEY"] = bool(
            settings.openai_api_key or os.environ.get("OPENAI_API_KEY")
        )
    elif mid.startswith("anthropic/"):
        key_checks["ANTHROPIC_API_KEY"] = bool(
            settings.anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY")
        )
    elif mid.startswith("gemini/"):
        key_checks["GOOGLE_API_KEY"] = bool(
            settings.google_api_key or os.environ.get("GOOGLE_API_KEY")
        )

    missing_keys = [k for k, present in key_checks.items() if not present]
    model_status = "ok" if (has_prefix and not missing_keys) else "warning"
    if missing_keys:
        overall = "degraded" if overall == "ok" else overall
    checks["model"] = {
        "status": model_status,
        "model_id": mid,
        "known_provider": has_prefix,
        "missing_keys": missing_keys,
    }

    # --- Config warnings --------------------------------------------------
    warnings: list[str] = []
    if not settings.vecna_scan_fee_usd and not settings.vecna_tool_unit_fee_usd:
        warnings.append("No platform fees configured (VECNA_SCAN_FEE_USD / VECNA_TOOL_UNIT_FEE_USD) — all runs are free.")
    if settings.vecna_hide_costs:
        warnings.append("VECNA_HIDE_COSTS=1: cost display is disabled for users.")
    cors = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
    if "*" in cors:
        warnings.append("CORS allows all origins (*) — restrict for production.")

    checks["config"] = {
        "status": "warning" if warnings else "ok",
        "warnings": warnings,
        "rate_limits": current_limits(),
    }

    elapsed_ms = round((time.monotonic() - _start) * 1000)
    return {"status": overall, "elapsed_ms": elapsed_ms, "checks": checks}


@app.get("/api/metrics", response_class=PlainTextResponse)
async def prometheus_metrics():
    """Prometheus-compatible text metrics for operations and billing aggregates."""
    async with session_factory() as session:
        rows = await session.execute(
            select(
                Operation.status,
                func.count(Operation.id).label("n"),
                func.sum(Operation.cost_usd).label("cost"),
                func.sum(Operation.billable_total_usd).label("billable"),
                func.sum(Operation.tool_units).label("tool_units"),
                func.sum(Operation.input_tokens).label("in_tok"),
                func.sum(Operation.output_tokens).label("out_tok"),
            ).group_by(Operation.status)
        )
        aggs = rows.all()

    lines: list[str] = [
        "# HELP vecna_operations_total Total operations by status",
        "# TYPE vecna_operations_total counter",
    ]
    total_cost = 0.0
    total_billable = 0.0
    total_tool_units = 0
    total_in = 0
    total_out = 0
    for row in aggs:
        status, n, cost, billable, tu, in_tok, out_tok = row
        lines.append(f'vecna_operations_total{{status="{status}"}} {n}')
        total_cost += float(cost or 0)
        total_billable += float(billable or 0)
        total_tool_units += int(tu or 0)
        total_in += int(in_tok or 0)
        total_out += int(out_tok or 0)

    lines += [
        "",
        "# HELP vecna_llm_cost_usd_total Cumulative LLM token cost in USD",
        "# TYPE vecna_llm_cost_usd_total counter",
        f"vecna_llm_cost_usd_total {total_cost:.6f}",
        "",
        "# HELP vecna_billable_usd_total Cumulative billable total in USD",
        "# TYPE vecna_billable_usd_total counter",
        f"vecna_billable_usd_total {total_billable:.6f}",
        "",
        "# HELP vecna_tool_calls_total Cumulative tool invocations",
        "# TYPE vecna_tool_calls_total counter",
        f"vecna_tool_calls_total {total_tool_units}",
        "",
        "# HELP vecna_llm_input_tokens_total Cumulative LLM input tokens",
        "# TYPE vecna_llm_input_tokens_total counter",
        f"vecna_llm_input_tokens_total {total_in}",
        "",
        "# HELP vecna_llm_output_tokens_total Cumulative LLM output tokens",
        "# TYPE vecna_llm_output_tokens_total counter",
        f"vecna_llm_output_tokens_total {total_out}",
        "",
    ]
    return "\n".join(lines)


@app.get("/health")
def health():
    return {"status": "ok"}
