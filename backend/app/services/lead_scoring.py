"""Lead-scoring: ICP gate + intent signals + routing matrix.

Source rubrics:
  icp-scoring-rubric.md  — firmographic/technographic attrs + negative signals
  lead-scoring-model.md  — intent behaviors (caller handles time-decay) + routing
  meddpicc-qualification.md — deal-health context surfaced in output

Feature flag: LEAD_SCORING_ENABLED=1; kill-switch: LEAD_SCORING_KILL_SWITCH=1.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from typing import Literal


def lead_scoring_enabled() -> bool:
    if os.environ.get("LEAD_SCORING_KILL_SWITCH", "").strip() in ("1", "true", "yes"):
        return False
    return os.environ.get("LEAD_SCORING_ENABLED", "").strip() in ("1", "true", "yes")


class FirmographicAttribute(str, Enum):
    COMPANY_SIZE_OK  = "company_size_200_5000"
    INDUSTRY_IDEAL   = "industry_ideal"
    REVENUE_OK       = "annual_revenue_20m_1b"
    TECH_STACK_OK    = "tech_stack_cloud_native"
    REGION_OK        = "region_us_eu"
    TOOLING_GAP      = "no_existing_agent_platform"

ATTRIBUTE_POINTS: dict[FirmographicAttribute, int] = {
    FirmographicAttribute.COMPANY_SIZE_OK: 25,   # 200–5 000 employees
    FirmographicAttribute.INDUSTRY_IDEAL:  20,   # FinTech/SaaS/e-com/regulated
    FirmographicAttribute.REVENUE_OK:      15,   # $20M–$1B ARR
    FirmographicAttribute.TECH_STACK_OK:   15,   # cloud-native + SRE team
    FirmographicAttribute.REGION_OK:       10,   # US/EU supported data residency
    FirmographicAttribute.TOOLING_GAP:     15,   # no current agent platform
}


class NegativeSignal(str, Enum):
    TINY_COMPANY        = "lt_50_employees_preseed"
    PERSONAL_DOMAIN     = "student_personal_free_email"
    BAD_INDUSTRY        = "industry_we_cant_serve"
    COMPETITOR_PATTERN  = "competitor_tirekicker"
    NO_BUDGET_AUTHORITY = "no_budget_authority"

NEGATIVE_POINTS: dict[NegativeSignal, int] = {
    NegativeSignal.TINY_COMPANY:        -30,   # <50 emp / pre-seed
    NegativeSignal.PERSONAL_DOMAIN:     -40,   # student/personal/free-email
    NegativeSignal.BAD_INDUSTRY:        -25,   # regulated we can't support
    NegativeSignal.COMPETITOR_PATTERN:  -20,   # competitor employee / tire-kicker
    NegativeSignal.NO_BUDGET_AUTHORITY: -20,   # no budget authority anywhere
}


class IntentSignal(str, Enum):
    PRICING_PAGE_VISIT   = "pricing_page_visit"        # +20 / 14-day decay
    DEMO_BOOKED_ATTENDED = "demo_booked_attended"      # +30 / 30-day decay
    DOCS_API_DEEP_READ   = "docs_api_deep_read_3plus"  # +15 / 14-day decay
    COMPETITOR_COMPARE   = "competitor_compare_page"   # +15 / 14-day decay
    FREE_TRIAL_SIGNUP    = "free_trial_signup"         # +25 / 30-day decay
    MULTI_PERSON_VISIT   = "repeat_visit_2plus_people" # +20 / 14-day decay
    EMAIL_OPEN_COLD      = "email_open_cold_list"      # +2  /  7-day decay
    UNSUBSCRIBE          = "unsubscribe_not_now"       # -15 / no decay

INTENT_POINTS: dict[IntentSignal, int] = {
    IntentSignal.PRICING_PAGE_VISIT:   20,
    IntentSignal.DEMO_BOOKED_ATTENDED: 30,
    IntentSignal.DOCS_API_DEEP_READ:   15,
    IntentSignal.COMPETITOR_COMPARE:   15,
    IntentSignal.FREE_TRIAL_SIGNUP:    25,
    IntentSignal.MULTI_PERSON_VISIT:   20,
    IntentSignal.EMAIL_OPEN_COLD:       2,
    IntentSignal.UNSUBSCRIBE:         -15,
}

INTENT_HIGH_THRESHOLD = 30

FitBand     = Literal["A", "B", "C"]
IntentLevel = Literal["high", "low"]

ROUTING_MATRIX: dict[tuple[FitBand, IntentLevel], dict] = {
    ("A", "high"): {"action": "mql_to_sql",       "owner": "ae",        "sla_hours": 8,
                    "label": "MQL -> SQL now. Route to AE same day."},
    ("A", "low"):  {"action": "nurture_targeted",  "owner": "marketing", "sla_hours": None,
                    "label": "Nurture with targeted content; sales-assist on next signal."},
    ("B", "high"): {"action": "ae_qualify_hard",   "owner": "ae",        "sla_hours": 48,
                    "label": "AE qualifies hard (champion + pain required)."},
    ("B", "low"):  {"action": "marketing_nurture", "owner": "marketing", "sla_hours": None,
                    "label": "Marketing nurture; re-score monthly."},
    ("C", "high"): {"action": "self_serve_plg",    "owner": "plg",       "sla_hours": None,
                    "label": "Self-serve / PLG; do not staff an AE."},
    ("C", "low"):  {"action": "suppress",          "owner": "none",      "sla_hours": None,
                    "label": "Suppress."},
}


@dataclass
class LeadScoreResult:
    fit_score:        int
    fit_band:         FitBand
    fit_attributes:   list[str]
    negative_signals: list[str]
    intent_score:     int
    intent_level:     IntentLevel
    intent_signals:   list[str]
    routing:          dict
    meddpicc_note:    str


def score_lead(
    attributes: list[FirmographicAttribute],
    negatives:  list[NegativeSignal],
    intent:     list[IntentSignal],
) -> LeadScoreResult:
    fit_score = (
        sum(ATTRIBUTE_POINTS.get(a, 0) for a in attributes)
        + sum(NEGATIVE_POINTS.get(n, 0) for n in negatives)
    )
    fit_band: FitBand = "A" if fit_score >= 70 else ("B" if fit_score >= 40 else "C")

    intent_score = sum(INTENT_POINTS.get(s, 0) for s in intent)
    intent_level: IntentLevel = "high" if intent_score >= INTENT_HIGH_THRESHOLD else "low"

    routing = ROUTING_MATRIX[(fit_band, intent_level)]

    if fit_band == "A" and intent_level == "high":
        note = "Begin MEDDPICC discovery immediately. Confirm EB and champion before first forecast commit."
    elif fit_band == "B" and intent_level == "high":
        note = "Gate on champion test and pain tied to a metric before advancing."
    elif fit_band in ("A", "B"):
        note = "Do not open MEDDPICC until intent score crosses threshold."
    else:
        note = "ICP gate failed -- do not enter MEDDPICC discovery."

    return LeadScoreResult(
        fit_score=fit_score, fit_band=fit_band,
        fit_attributes=[a.value for a in attributes],
        negative_signals=[n.value for n in negatives],
        intent_score=intent_score, intent_level=intent_level,
        intent_signals=[s.value for s in intent],
        routing=routing, meddpicc_note=note,
    )
