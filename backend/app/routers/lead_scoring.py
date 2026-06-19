"""Lead-scoring API router.

GET  /api/lead-scoring/status  -- feature-flag probe (always responds)
GET  /api/lead-scoring/rubric  -- encoded ICP rubric + intent table
POST /api/lead-scoring/score   -- score a single lead

Returns 503 when flag is off or kill-switch is set.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services.lead_scoring import (
    ATTRIBUTE_POINTS,
    INTENT_HIGH_THRESHOLD,
    INTENT_POINTS,
    NEGATIVE_POINTS,
    ROUTING_MATRIX,
    FirmographicAttribute,
    IntentSignal,
    NegativeSignal,
    lead_scoring_enabled,
    score_lead,
)

router = APIRouter(prefix="/api/lead-scoring", tags=["lead-scoring"])


def _require_enabled() -> None:
    if not lead_scoring_enabled():
        raise HTTPException(
            status_code=503,
            detail="Lead-scoring is disabled. Set LEAD_SCORING_ENABLED=1 to enable.",
        )


class ScoreRequest(BaseModel):
    attributes:     list[FirmographicAttribute] = Field(default_factory=list)
    negatives:      list[NegativeSignal]        = Field(default_factory=list)
    intent_signals: list[IntentSignal]          = Field(default_factory=list)


class RoutingOut(BaseModel):
    action:    str
    label:     str
    owner:     str
    sla_hours: int | None


class ScoreResponse(BaseModel):
    fit_score:        int
    fit_band:         str
    fit_attributes:   list[str]
    negative_signals: list[str]
    intent_score:     int
    intent_level:     str
    intent_signals:   list[str]
    routing:          RoutingOut
    meddpicc_note:    str


@router.get("/status", summary="Feature-flag probe")
def lead_scoring_status() -> dict:
    return {"enabled": lead_scoring_enabled()}


@router.get("/rubric", summary="Encoded ICP rubric and intent table")
def get_rubric() -> dict:
    _require_enabled()
    return {
        "firmographic_attributes": {k.value: v for k, v in ATTRIBUTE_POINTS.items()},
        "negative_signals":        {k.value: v for k, v in NEGATIVE_POINTS.items()},
        "intent_signals":          {k.value: v for k, v in INTENT_POINTS.items()},
        "intent_high_threshold":   INTENT_HIGH_THRESHOLD,
        "fit_bands":               {"A": ">=70", "B": "40-69", "C": "<40"},
        "routing_matrix": {
            f"{band}_{level}": route
            for (band, level), route in ROUTING_MATRIX.items()
        },
    }


@router.post("/score", response_model=ScoreResponse, summary="Score a lead")
def score_lead_endpoint(body: ScoreRequest) -> ScoreResponse:
    _require_enabled()
    r = score_lead(body.attributes, body.negatives, body.intent_signals)
    return ScoreResponse(
        fit_score=r.fit_score, fit_band=r.fit_band,
        fit_attributes=r.fit_attributes, negative_signals=r.negative_signals,
        intent_score=r.intent_score, intent_level=r.intent_level,
        intent_signals=r.intent_signals,
        routing=RoutingOut(**r.routing),
        meddpicc_note=r.meddpicc_note,
    )
