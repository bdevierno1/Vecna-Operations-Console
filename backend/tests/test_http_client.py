"""Tests for app.services.http_client — gateway detection, request headers, logging hooks."""

import json
import time
from unittest.mock import MagicMock, patch

import httpx
import pytest

from app.services.http_client import (
    DEFAULT_CONNECT_TIMEOUT,
    DEFAULT_READ_TIMEOUT,
    REQUEST_ID_HEADER,
    VECNA_USER_AGENT,
    _on_request,
    _on_response,
    detect_gateway_from_headers,
    detect_gateway_from_model_id,
    make_client,
    recon_client,
)


# ---------------------------------------------------------------------------
# detect_gateway_from_model_id
# ---------------------------------------------------------------------------

class TestDetectGatewayFromModelId:
    @pytest.mark.parametrize("model_id,expected", [
        ("openrouter/openai/gpt-4o",    "openrouter"),
        ("anthropic/claude-3-5-sonnet", "claude"),
        ("openai/gpt-4o-mini",          "openai"),
        ("gemini/gemini-1.5-flash",     "gemini"),
        ("bedrock/claude-3-haiku",      "bedrock"),
        ("vertex/claude-3-sonnet",      "vertex"),
        ("azure/gpt-4",                 "azure"),
        ("some-unknown-model",          None),
        ("",                            None),
    ])
    def test_model_id_detection(self, model_id, expected):
        assert detect_gateway_from_model_id(model_id) == expected


# ---------------------------------------------------------------------------
# detect_gateway_from_headers
# ---------------------------------------------------------------------------

class TestDetectGatewayFromHeaders:
    def _headers(self, raw: dict[str, str]) -> httpx.Headers:
        return httpx.Headers(raw)

    def test_litellm_prefix(self):
        h = self._headers({"x-litellm-call-id": "abc123"})
        assert detect_gateway_from_headers(h) == "litellm"

    def test_helicone_prefix(self):
        h = self._headers({"helicone-id": "xyz"})
        assert detect_gateway_from_headers(h) == "helicone"

    def test_portkey_prefix(self):
        h = self._headers({"x-portkey-trace-id": "t1"})
        assert detect_gateway_from_headers(h) == "portkey"

    def test_cloudflare_prefix(self):
        h = self._headers({"cf-aig-response-id": "rid"})
        assert detect_gateway_from_headers(h) == "cloudflare-ai-gateway"

    def test_openrouter_prefix(self):
        h = self._headers({"x-openrouter-version": "1.0"})
        assert detect_gateway_from_headers(h) == "openrouter"

    def test_unknown_headers_return_none(self):
        h = self._headers({"content-type": "application/json", "x-request-id": "abc"})
        assert detect_gateway_from_headers(h) is None

    def test_empty_headers_return_none(self):
        assert detect_gateway_from_headers(httpx.Headers({})) is None


# ---------------------------------------------------------------------------
# _on_request hook
# ---------------------------------------------------------------------------

class TestOnRequestHook:
    def _make_request(self) -> httpx.Request:
        return httpx.Request("GET", "https://example.com/path")

    def test_injects_request_id_header(self):
        req = self._make_request()
        _on_request(req)
        assert REQUEST_ID_HEADER in req.headers
        rid = req.headers[REQUEST_ID_HEADER]
        assert len(rid) == 36  # UUID format

    def test_request_ids_are_unique(self):
        reqs = [self._make_request() for _ in range(10)]
        for r in reqs:
            _on_request(r)
        ids = {r.headers[REQUEST_ID_HEADER] for r in reqs}
        assert len(ids) == 10

    def test_stores_start_time_in_extensions(self):
        req = self._make_request()
        before = time.monotonic()
        _on_request(req)
        after = time.monotonic()
        start = req.extensions.get("_vecna_start")
        assert start is not None
        assert before <= start <= after


# ---------------------------------------------------------------------------
# _on_response hook (log output)
# ---------------------------------------------------------------------------

class TestOnResponseHook:
    def _make_request_with_hook(self) -> httpx.Request:
        req = httpx.Request("GET", "https://crt.sh/path?q=test")
        _on_request(req)
        return req

    def test_logs_one_line(self):
        req = self._make_request_with_hook()
        resp = httpx.Response(200, request=req)
        with patch("app.services.http_client.logger") as mock_log:
            _on_response(resp)
            mock_log.info.assert_called_once()

    def test_log_contains_expected_fields(self):
        req = self._make_request_with_hook()
        resp = httpx.Response(200, request=req)
        captured: list[str] = []
        with patch("app.services.http_client.logger") as mock_log:
            mock_log.info.side_effect = lambda fmt, line: captured.append(line)
            _on_response(resp)
        assert len(captured) == 1
        data = json.loads(captured[0])
        assert data["event"] == "tool_http_request"
        assert data["method"] == "GET"
        assert data["host"] == "crt.sh"
        assert data["path"] == "/path"
        assert data["status"] == 200
        assert isinstance(data["latency_ms"], int)
        assert data["request_id"] == req.headers[REQUEST_ID_HEADER]

    def test_detects_litellm_gateway_in_log(self):
        req = self._make_request_with_hook()
        resp = httpx.Response(200, headers={"x-litellm-call-id": "abc"}, request=req)
        captured: list[str] = []
        with patch("app.services.http_client.logger") as mock_log:
            mock_log.info.side_effect = lambda fmt, line: captured.append(line)
            _on_response(resp)
        data = json.loads(captured[0])
        assert data.get("gateway") == "litellm"

    def test_no_gateway_key_when_no_match(self):
        req = self._make_request_with_hook()
        resp = httpx.Response(200, request=req)
        captured: list[str] = []
        with patch("app.services.http_client.logger") as mock_log:
            mock_log.info.side_effect = lambda fmt, line: captured.append(line)
            _on_response(resp)
        data = json.loads(captured[0])
        assert "gateway" not in data


# ---------------------------------------------------------------------------
# make_client / recon_client
# ---------------------------------------------------------------------------

class TestMakeClient:
    def test_returns_httpx_client(self):
        client = make_client()
        assert isinstance(client, httpx.Client)
        client.close()

    def test_user_agent_header(self):
        client = make_client()
        assert client.headers.get("user-agent") == VECNA_USER_AGENT
        client.close()

    def test_timeout_defaults(self):
        client = make_client()
        assert client.timeout.connect == DEFAULT_CONNECT_TIMEOUT
        assert client.timeout.read == DEFAULT_READ_TIMEOUT
        client.close()

    def test_read_timeout_override(self):
        client = make_client(read_timeout=30.0)
        assert client.timeout.read == 30.0
        client.close()

    def test_request_hook_registered(self):
        client = make_client()
        assert _on_request in client.event_hooks["request"]
        client.close()

    def test_response_hook_registered(self):
        client = make_client()
        assert _on_response in client.event_hooks["response"]
        client.close()


class TestReconClient:
    def test_context_manager_yields_client(self):
        with recon_client() as client:
            assert isinstance(client, httpx.Client)

    def test_client_closed_after_context(self):
        with recon_client() as client:
            pass
        assert client.is_closed

    def test_read_timeout_passthrough(self):
        with recon_client(read_timeout=42.0) as client:
            assert client.timeout.read == 42.0

    def test_follow_redirects_false(self):
        with recon_client(follow_redirects=False) as client:
            assert client.follow_redirects is False
