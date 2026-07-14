"""SSRF-guard unit tests (app/url_guard.py) — the target_url gate for operations."""

from __future__ import annotations

import pytest

from app.url_guard import is_safe_public_target


def test_public_https_url_is_allowed() -> None:
    ok, reason = is_safe_public_target("https://example.com/path")
    assert ok is True
    assert reason == ""


def test_bare_host_gets_https_prepended_and_allowed() -> None:
    ok, _ = is_safe_public_target("example.com")
    assert ok is True


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost/x",
        "https://0.0.0.0",
        "http://metadata.google.internal/latest",
        "https://app.localhost",
    ],
)
def test_blocked_hostnames_and_localhost_suffix(url: str) -> None:
    ok, reason = is_safe_public_target(url)
    assert ok is False
    assert reason == "Host not allowed"


@pytest.mark.parametrize(
    "url",
    [
        "http://10.0.0.5",
        "http://192.168.1.1",
        "http://127.0.0.1",
        "http://169.254.169.254/latest/meta-data",
        "http://172.16.0.1",
    ],
)
def test_private_ipv4_ranges_blocked(url: str) -> None:
    ok, reason = is_safe_public_target(url)
    assert ok is False
    assert reason in {"IP range not allowed", "Host not allowed"}


def test_multicast_ipv4_blocked() -> None:
    ok, reason = is_safe_public_target("http://224.0.0.1")
    assert ok is False
    assert reason == "IP range not allowed"


def test_invalid_ipv4_literal_rejected() -> None:
    ok, reason = is_safe_public_target("http://999.999.999.999")
    assert ok is False
    assert reason == "Invalid IP"


def test_missing_host_rejected() -> None:
    ok, reason = is_safe_public_target("http://")
    assert ok is False
    assert reason == "Missing host"


def test_loopback_ipv6_host_rejected() -> None:
    # urlparse strips the brackets, yielding host "::1", caught as loopback IPv6.
    ok, reason = is_safe_public_target("http://[::1]:8080/")
    assert ok is False
    assert reason == "IP range not allowed"


def test_public_ipv4_literal_allowed() -> None:
    ok, reason = is_safe_public_target("http://8.8.8.8")
    assert ok is True
    assert reason == ""
