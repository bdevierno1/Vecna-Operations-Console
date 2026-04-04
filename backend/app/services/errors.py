"""Agent / LLM error classification, user messages, and retry policy.

Adapted for Python + LiteLLM + Strands, with stable tags for logging and retries.

Error types (for telemetry tagging):
  timeout            connection or read timeout
  rate_limit         HTTP 429
  server_overload    HTTP 529 / "overloaded_error"
  server_error       HTTP 5xx (other)
  auth_error         HTTP 401 / 403
  context_too_long   HTTP 400 prompt-too-long
  invalid_request    HTTP 400 (other)
  connection_error   socket / network failure
  unknown            anything else
"""

from __future__ import annotations

import math
import random
import re

# ---------------------------------------------------------------------------
# Error type constants (keep in sync with telemetry tags)
# ---------------------------------------------------------------------------
ET_TIMEOUT = "timeout"
ET_RATE_LIMIT = "rate_limit"
ET_SERVER_OVERLOAD = "server_overload"
ET_SERVER_ERROR = "server_error"
ET_AUTH_ERROR = "auth_error"
ET_CONTEXT_TOO_LONG = "context_too_long"
ET_INVALID_REQUEST = "invalid_request"
ET_CONNECTION = "connection_error"
ET_UNKNOWN = "unknown"


# ---------------------------------------------------------------------------
# Helpers: extract status / message from various exception shapes
# ---------------------------------------------------------------------------

def _status(exc: BaseException) -> int | None:
    for attr in ("status_code", "status", "code"):
        v = getattr(exc, attr, None)
        if isinstance(v, int):
            return v
    return None


def _message(exc: BaseException) -> str:
    return str(getattr(exc, "message", None) or exc) or ""


def _retry_after_seconds(exc: BaseException) -> float | None:
    """Parse Retry-After header value in seconds from LiteLLM exceptions."""
    for attr in ("retry_after", "llm_provider_retry_after"):
        v = getattr(exc, attr, None)
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                pass
    # Some wrappers embed it in the message: "retry after 30 seconds"
    msg = _message(exc)
    m = re.search(r"retry[-\s]?after[:\s]+(\d+(?:\.\d+)?)", msg, re.IGNORECASE)
    if m:
        return float(m.group(1))
    return None


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

def classify_agent_error(exc: BaseException) -> str:
    """Return a stable error-type string for telemetry and retry decisions.

    Works against Python exception hierarchies (LiteLLM, httpx, plain Python).
    """
    msg = _message(exc).lower()
    status = _status(exc)
    type_name = type(exc).__name__

    # --- Timeout -------------------------------------------------------
    if any(t in type_name.lower() for t in ("timeout", "timedout")):
        return ET_TIMEOUT
    if "timeout" in msg or "timed out" in msg:
        return ET_TIMEOUT

    # --- LiteLLM typed exceptions (class-name check is provider-agnostic) ---
    # litellm raises: RateLimitError, ServiceUnavailableError,
    #   AuthenticationError, ContextWindowExceededError, BadRequestError, etc.
    if "ratelimiterror" in type_name.lower() or status == 429:
        return ET_RATE_LIMIT
    if "serviceunavaila" in type_name.lower() or status == 529:
        return ET_SERVER_OVERLOAD
    if "overloaded" in msg:
        return ET_SERVER_OVERLOAD
    if "authenticationerror" in type_name.lower():
        return ET_AUTH_ERROR
    if "contextwindow" in type_name.lower() or "prompt is too long" in msg:
        return ET_CONTEXT_TOO_LONG
    if "badrequest" in type_name.lower() and status == 400:
        return ET_INVALID_REQUEST

    # --- HTTP status fallbacks -----------------------------------------
    if status == 401 or status == 403:
        return ET_AUTH_ERROR
    if status == 429:
        return ET_RATE_LIMIT
    if status == 529:
        return ET_SERVER_OVERLOAD
    if status is not None and status >= 500:
        return ET_SERVER_ERROR
    if status == 400:
        return ET_INVALID_REQUEST

    # --- Connection / network -----------------------------------------
    if any(t in type_name.lower() for t in ("connection", "network", "socket")):
        return ET_CONNECTION
    if any(t in msg for t in ("econnreset", "econnrefused", "network", "socket")):
        return ET_CONNECTION

    return ET_UNKNOWN


# ---------------------------------------------------------------------------
# User-facing messages (clean; no internal details)
# ---------------------------------------------------------------------------

_USER_MESSAGES: dict[str, str] = {
    ET_TIMEOUT: "The LLM request timed out — please retry.",
    ET_RATE_LIMIT: "Rate limit reached. Please wait a moment and try again.",
    ET_SERVER_OVERLOAD: "The LLM provider is overloaded. Retrying automatically…",
    ET_SERVER_ERROR: "The LLM provider returned a server error. Retrying…",
    ET_AUTH_ERROR: "Authentication failed — check LITELLM_MODEL_ID and your API key.",
    ET_CONTEXT_TOO_LONG: "Prompt is too long for this model's context window.",
    ET_INVALID_REQUEST: "The request was rejected by the LLM provider (invalid request).",
    ET_CONNECTION: "Could not connect to the LLM provider. Check your network/proxy.",
    ET_UNKNOWN: "An unexpected error occurred during the agent run.",
}


def user_message_for_error(exc: BaseException) -> str:
    """Return a clean user-facing string for a classified error.

    For auth errors we append the original message so the operator can
    diagnose (e.g. which key is wrong) without exposing secrets.
    """
    et = classify_agent_error(exc)
    base = _USER_MESSAGES.get(et, _USER_MESSAGES[ET_UNKNOWN])
    if et == ET_AUTH_ERROR:
        detail = _message(exc)[:200]
        return f"{base} Detail: {detail}"
    return base


# ---------------------------------------------------------------------------
# Retry policy (which error types warrant another attempt)
# ---------------------------------------------------------------------------

_RETRYABLE_TYPES = {ET_RATE_LIMIT, ET_SERVER_OVERLOAD, ET_SERVER_ERROR, ET_CONNECTION, ET_TIMEOUT}
# Auth and context-too-long are non-retryable — they need operator action.
_NON_RETRYABLE_TYPES = {ET_AUTH_ERROR, ET_CONTEXT_TOO_LONG, ET_INVALID_REQUEST}


def is_retryable(exc: BaseException) -> bool:
    """True if we should retry after this error."""
    et = classify_agent_error(exc)
    if et in _NON_RETRYABLE_TYPES:
        return False
    return et in _RETRYABLE_TYPES


def retry_delay_seconds(
    attempt: int,
    exc: BaseException,
    *,
    base_ms: float = 500.0,
    max_ms: float = 32_000.0,
) -> float:
    """Exponential backoff + jitter, respecting Retry-After header.

    Formula: base_ms * 2^(attempt-1), capped at max_ms, plus ±25% jitter.
    """
    ra = _retry_after_seconds(exc)
    if ra is not None:
        return float(ra)

    base = min(base_ms * math.pow(2.0, attempt - 1), max_ms)
    jitter = random.uniform(0, 0.25 * base)
    return (base + jitter) / 1000.0
