import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class BillingSession(Base):
    """Cumulative billing for a client session (browser tab / operator session)."""

    __tablename__ = "billing_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    # total_cost_usd = LLM/token $ only; total_billable_usd = LLM + scan + tool fees
    total_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    total_credits: Mapped[float] = mapped_column(Float, default=0.0)
    total_input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_cache_read_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_cache_write_tokens: Mapped[int] = mapped_column(Integer, default=0)
    operation_count: Mapped[int] = mapped_column(Integer, default=0)
    wall_time_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    total_scan_fees_usd: Mapped[float] = mapped_column(Float, default=0.0)
    total_tool_fees_usd: Mapped[float] = mapped_column(Float, default=0.0)
    total_tool_units: Mapped[int] = mapped_column(Integer, default=0)
    total_billable_usd: Mapped[float] = mapped_column(Float, default=0.0)
    model_usage_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Operation(Base):
    __tablename__ = "operations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    session_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("billing_sessions.id", ondelete="SET NULL"),
        nullable=True,
    )
    target_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="pending")  # pending, running, completed, failed
    credits_used: Mapped[float] = mapped_column(Float, default=0.0)
    # LLM/token $ (app.cost); billable adds scan + tool fees
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    scan_fee_usd: Mapped[float] = mapped_column(Float, default=0.0)
    tool_fee_usd: Mapped[float] = mapped_column(Float, default=0.0)
    tool_units: Mapped[int] = mapped_column(Integer, default=0)
    billable_total_usd: Mapped[float] = mapped_column(Float, default=0.0)
    pricing_breakdown_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_write_tokens: Mapped[int] = mapped_column(Integer, default=0)
    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    cost_estimate_unknown: Mapped[bool] = mapped_column(Boolean, default=False)
    summary_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    events_json: Mapped[list | None] = mapped_column(JSON, nullable=True)  # streamed events log
    findings_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
