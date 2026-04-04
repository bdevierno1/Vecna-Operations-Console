from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app


def test_health() -> None:
    with TestClient(app) as c:
        r = c.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_public_config() -> None:
    with TestClient(app) as c:
        r = c.get("/api/config")
    assert r.status_code == 200
    body = r.json()
    assert "litellm_model_id" in body
    assert "billing_visible" in body
    assert body["billing_visible"] is True
    assert "vecna_scan_fee_usd" in body
    assert "vecna_tool_unit_fee_usd" in body
    assert "tool_pricing" in body
    assert "families" in body["tool_pricing"]


def test_post_operation_rejects_localhost() -> None:
    with TestClient(app) as c:
        r = c.post("/api/operations", json={"target_url": "http://localhost/foo"})
    assert r.status_code == 400


def test_list_operations_empty() -> None:
    with TestClient(app) as c:
        r = c.get("/api/operations")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_billing_session_create_and_get() -> None:
    # One client context: each new TestClient runs app lifespan (engine dispose); split clients can miss DB visibility.
    with TestClient(app) as c:
        r = c.post("/api/billing/sessions")
        assert r.status_code == 200
        sid = r.json()["session_id"]
        assert len(sid) == 36

        r2 = c.get(f"/api/billing/sessions/{sid}")
        assert r2.status_code == 200
        body = r2.json()
        assert body["session_id"] == sid
        assert body["total_cost_usd"] == 0.0
        assert body["operation_count"] == 0


def test_unknown_billing_session_404() -> None:
    with TestClient(app) as c:
        r = c.get("/api/billing/sessions/00000000-0000-0000-0000-000000000000")
    assert r.status_code == 404


def test_report_unknown_operation_404() -> None:
    with TestClient(app) as c:
        r = c.get("/api/operations/00000000-0000-0000-0000-000000000000/report.json")
    assert r.status_code == 404


def test_human_line_on_serialized_event() -> None:
    from app.lib.serialize import event_to_jsonable

    ev = event_to_jsonable({"init_event_loop": True})
    assert "human_line" in ev
    assert "Initializing" in ev["human_line"]

    reasoning = event_to_jsonable(
        {
            "reasoning": True,
            "reasoningText": " plan next step",
            "delta": {"reasoningContent": {"text": " plan next step"}},
        }
    )
    assert "Reasoning" in reasoning["human_line"]
    assert "plan next step" in reasoning["human_line"]
    assert "agent" not in reasoning
