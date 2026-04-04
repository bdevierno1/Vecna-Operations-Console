"""Centralized HTTP client for outbound recon tool calls.

Provides per-request IDs, default headers, timeouts, gateway hints from
response headers, and structured request telemetry.

All tool-level outbound HTTP should go through `recon_client()` rather
than constructing a bare httpx.Client, so we get consistent:
  - User-Agent: vecna-ops/1.0 (+recon)
  - X-Vecna-Request-Id: <uuid>   per request (survives timeouts for log correlation)
  - Connect timeout: 5 s  (fast-fail on dead hosts)
  - Read timeout:   15 s  (overridable per tool)
  - One structured log line per response: method, host, path, status, latency_ms
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from contextlib import contextmanager
from typing import Generator

import httpx

logger = logging.getLogger("vecna.http")

VECNA_USER_AGENT = "vecna-ops/1.0 (+recon)"
DEFAULT_CONNECT_TIMEOUT: float = 5.0
DEFAULT_READ_TIMEOUT: float = 15.0
REQUEST_ID_HEADER = "X-Vecna-Request-Id"

# ---------------------------------------------------------------------------
# Gateway / proxy detection
# ---------------------------------------------------------------------------
# Header-prefix fingerprints for proxy / gateway detection.
_GATEWAY_HEADER_PREFIXES: dict[str, list[str]] = {
    "litellm":               ["x-litellm-"],
    "helicone":              ["helicone-"],
    "portkey":               ["x-portkey-"],
    "openrouter":            ["x-openrouter-"],
    "cloudflare-ai-gateway": ["cf-aig-"],
    "kong":                  ["x-kong-"],
    "braintrust":            ["x-bt-"],
}

# Model-ID prefix → gateway name (used for LLM-call telemetry, not tool HTTP)
_GATEWAY_MODEL_PREFIXES: dict[str, str] = {
    "openrouter/": "openrouter",
    "bedrock/":    "bedrock",
    "vertex/":     "vertex",
    "azure/":      "azure",
    "anthropic/":  "claude",
    "openai/":     "openai",
    "gemini/":     "gemini",
}


def detect_gateway_from_model_id(model_id: str) -> str | None:
    """Return a stable gateway/provider name from the LiteLLM model ID prefix."""
    for prefix, gw in _GATEWAY_MODEL_PREFIXES.items():
        if model_id.startswith(prefix):
            return gw
    return None


def detect_gateway_from_headers(headers: httpx.Headers) -> str | None:
    """Return the first matching gateway name from HTTP response headers.

    First matching fingerprint wins.
    """
    header_names = [k.lower() for k in headers.keys()]
    for gw, prefixes in _GATEWAY_HEADER_PREFIXES.items():
        if any(h.startswith(p) for h in header_names for p in prefixes):
            return gw
    return None


# ---------------------------------------------------------------------------
# httpx event hooks — request id + latency logging
# ---------------------------------------------------------------------------

def _on_request(request: httpx.Request) -> None:
    """Inject a per-request UUID and record start time."""
    rid = str(uuid.uuid4())
    request.headers[REQUEST_ID_HEADER] = rid
    # httpx extensions dict is mutable and travels with the request object
    request.extensions["_vecna_start"] = time.monotonic()


def _on_response(response: httpx.Response) -> None:
    """Emit one structured log line per completed response."""
    start: float | None = response.request.extensions.get("_vecna_start")
    latency_ms = round((time.monotonic() - start) * 1000) if start is not None else None
    rid = response.request.headers.get(REQUEST_ID_HEADER, "")
    gateway = detect_gateway_from_headers(response.headers)
    logger.info(
        "%s",
        json.dumps(
            {
                "event": "tool_http_request",
                "method": response.request.method,
                "host": response.request.url.host,
                "path": str(response.request.url.path) or "/",
                "status": response.status_code,
                "latency_ms": latency_ms,
                "request_id": rid,
                **({"gateway": gateway} if gateway else {}),
            },
            default=str,
        ),
    )


# ---------------------------------------------------------------------------
# Public factory
# ---------------------------------------------------------------------------

def make_client(
    *,
    read_timeout: float = DEFAULT_READ_TIMEOUT,
    follow_redirects: bool = True,
) -> httpx.Client:
    """Return a configured httpx.Client (caller owns lifecycle).

    Prefer `recon_client()` context manager for automatic cleanup.
    """
    return httpx.Client(
        timeout=httpx.Timeout(DEFAULT_CONNECT_TIMEOUT, read=read_timeout),
        follow_redirects=follow_redirects,
        headers={"User-Agent": VECNA_USER_AGENT},
        event_hooks={
            "request": [_on_request],
            "response": [_on_response],
        },
    )


@contextmanager
def recon_client(
    *,
    read_timeout: float = DEFAULT_READ_TIMEOUT,
    follow_redirects: bool = True,
) -> Generator[httpx.Client, None, None]:
    """Context manager yielding a configured recon HTTP client.

    Drop-in replacement for `with httpx.Client(...) as client:` in tools.
    Adds User-Agent, X-Vecna-Request-Id, timeout defaults, and structured
    response logging automatically.

    Usage:
        with recon_client(read_timeout=30.0) as client:
            r = client.get(url)
    """
    with make_client(read_timeout=read_timeout, follow_redirects=follow_redirects) as client:
        yield client
