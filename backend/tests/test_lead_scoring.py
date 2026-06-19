"""Tests: lead-scoring service (fit, intent, routing) + API feature-flag behavior."""
from __future__ import annotations

import os

from fastapi.testclient import TestClient

from app.services.lead_scoring import (
    FirmographicAttribute as FA,
    IntentSignal as IS,
    NegativeSignal as NS,
    score_lead,
)


class TestFitScoring:
    def test_all_positives_band_a(self):
        r = score_lead(list(FA), [], [])
        assert r.fit_score == 100 and r.fit_band == "A"

    def test_band_b_boundary(self):
        r = score_lead([FA.COMPANY_SIZE_OK, FA.REVENUE_OK], [], [])  # 25+15=40
        assert r.fit_score == 40 and r.fit_band == "B"

    def test_negative_signals_subtract(self):
        r = score_lead([FA.REGION_OK], [NS.PERSONAL_DOMAIN], [])  # 10-40=-30
        assert r.fit_score == -30 and r.fit_band == "C"

    def test_combined_stays_a(self):
        r = score_lead(list(FA), [NS.TINY_COMPANY], [])  # 100-30=70
        assert r.fit_score == 70 and r.fit_band == "A"


class TestIntentClassification:
    def test_demo_alone_is_high(self):
        r = score_lead([], [], [IS.DEMO_BOOKED_ATTENDED])
        assert r.intent_score == 30 and r.intent_level == "high"

    def test_cold_email_is_low(self):
        r = score_lead([], [], [IS.EMAIL_OPEN_COLD])
        assert r.intent_score == 2 and r.intent_level == "low"

    def test_unsubscribe_drags_down(self):
        r = score_lead([], [], [IS.PRICING_PAGE_VISIT, IS.UNSUBSCRIBE])  # 20-15=5
        assert r.intent_score == 5 and r.intent_level == "low"

    def test_two_signals_cross_threshold(self):
        r = score_lead([], [], [IS.PRICING_PAGE_VISIT, IS.DOCS_API_DEEP_READ])  # 35
        assert r.intent_level == "high"


class TestRoutingMatrix:
    def test_a_high_mql_to_sql(self):
        r = score_lead(list(FA), [], [IS.DEMO_BOOKED_ATTENDED])
        assert r.routing["action"] == "mql_to_sql" and r.routing["sla_hours"] == 8

    def test_a_low_nurture(self):
        r = score_lead(list(FA), [], [])
        assert r.routing["action"] == "nurture_targeted"

    def test_b_high_qualify_hard(self):
        r = score_lead([FA.COMPANY_SIZE_OK, FA.REVENUE_OK], [], [IS.DEMO_BOOKED_ATTENDED])
        assert r.fit_band == "B" and r.routing["action"] == "ae_qualify_hard"

    def test_c_high_self_serve(self):
        r = score_lead([], [], [IS.DEMO_BOOKED_ATTENDED])
        assert r.fit_band == "C" and r.routing["action"] == "self_serve_plg"

    def test_c_low_suppress_and_no_meddpicc(self):
        r = score_lead([], [], [])
        assert r.routing["action"] == "suppress"
        assert "ICP gate failed" in r.meddpicc_note

    def test_meddpicc_note_a_high_start_immediately(self):
        r = score_lead(list(FA), [], [IS.DEMO_BOOKED_ATTENDED])
        assert "immediately" in r.meddpicc_note


from app.main import app  # noqa: E402
client = TestClient(app)


class TestFeatureFlag:
    def test_status_always_ok(self):
        r = client.get("/api/lead-scoring/status")
        assert r.status_code == 200 and "enabled" in r.json()

    def test_disabled_by_default(self):
        os.environ.pop("LEAD_SCORING_ENABLED", None)
        os.environ.pop("LEAD_SCORING_KILL_SWITCH", None)
        assert client.post("/api/lead-scoring/score", json={}).status_code == 503

    def test_enabled_scores_a_high(self):
        os.environ["LEAD_SCORING_ENABLED"] = "1"
        os.environ.pop("LEAD_SCORING_KILL_SWITCH", None)
        try:
            r = client.post("/api/lead-scoring/score", json={
                "attributes": [a.value for a in FA],
                "negatives": [],
                "intent_signals": [IS.DEMO_BOOKED_ATTENDED.value],
            })
            assert r.status_code == 200
            assert r.json()["routing"]["action"] == "mql_to_sql"
        finally:
            os.environ.pop("LEAD_SCORING_ENABLED", None)

    def test_kill_switch_overrides_enabled(self):
        os.environ["LEAD_SCORING_ENABLED"] = "1"
        os.environ["LEAD_SCORING_KILL_SWITCH"] = "1"
        try:
            assert client.post("/api/lead-scoring/score", json={}).status_code == 503
        finally:
            os.environ.pop("LEAD_SCORING_ENABLED", None)
            os.environ.pop("LEAD_SCORING_KILL_SWITCH", None)

    def test_rubric_returns_matrix(self):
        os.environ["LEAD_SCORING_ENABLED"] = "1"
        try:
            r = client.get("/api/lead-scoring/rubric")
            assert r.status_code == 200
            assert r.json()["fit_bands"]["A"] == ">=70"
            assert "A_high" in r.json()["routing_matrix"]
        finally:
            os.environ.pop("LEAD_SCORING_ENABLED", None)
