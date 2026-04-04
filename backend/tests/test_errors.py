"""Tests for app.services.errors — classification, user messages, retry policy, backoff.

Tests error classification and retry delay behavior for the agent runner.
"""
import math
import pytest

from app.services.errors import (
    ET_AUTH_ERROR,
    ET_CONNECTION,
    ET_CONTEXT_TOO_LONG,
    ET_INVALID_REQUEST,
    ET_RATE_LIMIT,
    ET_SERVER_ERROR,
    ET_SERVER_OVERLOAD,
    ET_TIMEOUT,
    ET_UNKNOWN,
    classify_agent_error,
    is_retryable,
    retry_delay_seconds,
    user_message_for_error,
)


# ---------------------------------------------------------------------------
# Fake exception helpers
# ---------------------------------------------------------------------------

class _FakeAPIError(Exception):
    def __init__(self, status_code: int, message: str = ""):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


class _FakeRateLimitError(_FakeAPIError):
    pass


class _FakeServiceUnavailableError(_FakeAPIError):
    pass


class _FakeAuthenticationError(_FakeAPIError):
    pass


class _FakeContextWindowExceededError(_FakeAPIError):
    pass


class _FakeTimeoutError(Exception):
    pass


class _FakeConnectionError(Exception):
    pass


# ---------------------------------------------------------------------------
# classify_agent_error
# ---------------------------------------------------------------------------

class TestClassifyAgentError:
    def test_http_429_classified_as_rate_limit(self):
        assert classify_agent_error(_FakeAPIError(429, "Too many requests")) == ET_RATE_LIMIT

    def test_ratelimiterror_class_name(self):
        assert classify_agent_error(_FakeRateLimitError(429)) == ET_RATE_LIMIT

    def test_http_529_classified_as_server_overload(self):
        assert classify_agent_error(_FakeAPIError(529)) == ET_SERVER_OVERLOAD

    def test_serviceunavailable_class_name(self):
        assert classify_agent_error(_FakeServiceUnavailableError(503)) == ET_SERVER_OVERLOAD

    def test_overloaded_in_message(self):
        err = Exception("The model is overloaded right now")
        assert classify_agent_error(err) == ET_SERVER_OVERLOAD

    def test_http_401_auth(self):
        assert classify_agent_error(_FakeAPIError(401, "Unauthorized")) == ET_AUTH_ERROR

    def test_http_403_auth(self):
        assert classify_agent_error(_FakeAPIError(403, "Forbidden")) == ET_AUTH_ERROR

    def test_authenticationerror_class_name(self):
        assert classify_agent_error(_FakeAuthenticationError(401)) == ET_AUTH_ERROR

    def test_context_window_class_name(self):
        assert classify_agent_error(_FakeContextWindowExceededError(400)) == ET_CONTEXT_TOO_LONG

    def test_prompt_too_long_message(self):
        err = Exception("prompt is too long: 200k tokens > 100k max")
        assert classify_agent_error(err) == ET_CONTEXT_TOO_LONG

    def test_http_400_invalid_request(self):
        err = _FakeAPIError(400, "Invalid JSON in request body")
        # Use _FakeBadRequestError-style name check
        err.__class__.__name__ = "BadRequestError"
        assert classify_agent_error(err) == ET_INVALID_REQUEST

    def test_http_500_server_error(self):
        assert classify_agent_error(_FakeAPIError(500, "Internal Server Error")) == ET_SERVER_ERROR

    def test_timeout_class_name(self):
        assert classify_agent_error(_FakeTimeoutError("deadline exceeded")) == ET_TIMEOUT

    def test_timeout_in_message(self):
        assert classify_agent_error(Exception("request timed out after 30s")) == ET_TIMEOUT

    def test_connection_class_name(self):
        assert classify_agent_error(_FakeConnectionError("ECONNRESET")) == ET_CONNECTION

    def test_unknown_for_plain_exception(self):
        assert classify_agent_error(ValueError("something broke")) == ET_UNKNOWN


# ---------------------------------------------------------------------------
# is_retryable
# ---------------------------------------------------------------------------

class TestIsRetryable:
    @pytest.mark.parametrize("exc,expected", [
        (_FakeAPIError(429), True),          # rate limit
        (_FakeAPIError(529), True),          # server overload
        (_FakeAPIError(500), True),          # server error
        (_FakeAPIError(503), True),          # server error
        (_FakeTimeoutError(), True),         # timeout
        (_FakeConnectionError(), True),      # connection
        (_FakeAPIError(401), False),         # auth — non-retryable
        (_FakeAPIError(403), False),         # auth — non-retryable
        (_FakeAPIError(400), False),         # invalid request — non-retryable (generic 400)
    ])
    def test_retryable_decisions(self, exc, expected):
        assert is_retryable(exc) is expected

    def test_context_window_not_retryable(self):
        err = Exception("prompt is too long: 200k tokens > 100k max")
        assert is_retryable(err) is False


# ---------------------------------------------------------------------------
# retry_delay_seconds
# ---------------------------------------------------------------------------

class TestRetryDelaySeconds:
    def test_attempt_1_base(self):
        delay = retry_delay_seconds(1, Exception(), base_ms=500.0, max_ms=32_000.0)
        # base = 500ms = 0.5s; with ≤25% jitter → [0.5, 0.625]
        assert 0.499 <= delay <= 0.63

    def test_exponential_growth(self):
        d1 = retry_delay_seconds(1, Exception(), base_ms=500.0, max_ms=32_000.0)
        d3 = retry_delay_seconds(3, Exception(), base_ms=500.0, max_ms=32_000.0)
        # attempt 3 base = 500 * 2^2 = 2000ms = 2s; much larger than attempt 1
        assert d3 > d1 * 2

    def test_capped_at_max(self):
        delay = retry_delay_seconds(20, Exception(), base_ms=500.0, max_ms=32_000.0)
        # max base = 32s, jitter ≤25% → ≤40s
        assert delay <= 40.0

    def test_retry_after_header_respected(self):
        class _Exc(Exception):
            retry_after = "10"

        delay = retry_delay_seconds(1, _Exc(), base_ms=500.0, max_ms=32_000.0)
        assert delay == pytest.approx(10.0)

    def test_retry_after_in_message(self):
        exc = Exception("Rate limit exceeded, retry-after: 5 seconds")
        delay = retry_delay_seconds(1, exc, base_ms=500.0, max_ms=32_000.0)
        assert delay == pytest.approx(5.0)


# ---------------------------------------------------------------------------
# user_message_for_error
# ---------------------------------------------------------------------------

class TestUserMessageForError:
    def test_rate_limit_message(self):
        msg = user_message_for_error(_FakeAPIError(429))
        assert "rate limit" in msg.lower() or "wait" in msg.lower()

    def test_timeout_message(self):
        msg = user_message_for_error(_FakeTimeoutError())
        assert "timed out" in msg.lower() or "timeout" in msg.lower()

    def test_auth_message_includes_detail(self):
        exc = _FakeAuthenticationError(401)
        exc.message = "invalid api key format"
        msg = user_message_for_error(exc)
        assert "detail" in msg.lower() or "invalid api key" in msg.lower()

    def test_context_too_long_message(self):
        exc = Exception("prompt is too long: 150000 > 100000")
        msg = user_message_for_error(exc)
        assert "context" in msg.lower() or "too long" in msg.lower()

    def test_returns_string_for_unknown(self):
        msg = user_message_for_error(ValueError("unexpected"))
        assert isinstance(msg, str) and len(msg) > 0
