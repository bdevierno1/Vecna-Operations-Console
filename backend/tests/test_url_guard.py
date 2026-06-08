"""Tests for SSRF guard — url_guard.is_safe_public_target.

Verifies that all RFC 1918 private ranges, loopback, link-local, metadata
endpoints, and the previously-missing 172.20–172.31 block are blocked.
"""

from __future__ import annotations

import pytest

from app.url_guard import is_safe_public_target


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _blocked(url: str) -> bool:
    ok, _ = is_safe_public_target(url)
    return not ok


def _allowed(url: str) -> bool:
    ok, _ = is_safe_public_target(url)
    return ok


# ---------------------------------------------------------------------------
# Should be BLOCKED
# ---------------------------------------------------------------------------

class TestBlocked:
    def test_localhost_name(self):
        assert _blocked("http://localhost/")

    def test_localhost_name_https(self):
        assert _blocked("https://localhost/path")

    def test_loopback_ipv4(self):
        assert _blocked("http://127.0.0.1/")

    def test_loopback_ipv4_other(self):
        assert _blocked("http://127.100.200.1/")

    def test_link_local_aws_metadata(self):
        # Classic AWS / many clouds instance-metadata endpoint
        assert _blocked("http://169.254.169.254/latest/meta-data/")

    def test_link_local_arbitrary(self):
        assert _blocked("http://169.254.10.5/internal")

    def test_rfc1918_10_block(self):
        assert _blocked("http://10.0.0.1/")

    def test_rfc1918_10_block_deep(self):
        assert _blocked("http://10.255.255.255/")

    def test_rfc1918_192_168(self):
        assert _blocked("http://192.168.1.1/admin")

    def test_rfc1918_172_16(self):
        assert _blocked("http://172.16.0.1/")

    def test_rfc1918_172_19(self):
        # Was covered before the fix; must still be blocked
        assert _blocked("http://172.19.255.1/")

    # --- Previously-missing 172.20–172.31 range (the core fix) ---

    def test_rfc1918_172_20(self):
        assert _blocked("http://172.20.0.1/")

    def test_rfc1918_172_25(self):
        assert _blocked("http://172.25.10.5/api")

    def test_rfc1918_172_31(self):
        assert _blocked("http://172.31.255.255/")

    def test_rfc1918_172_20_hostname_prefix(self):
        # Hostname whose dotted prefix looks like the private range
        assert _blocked("http://172.20.proxy.local/")

    def test_rfc1918_172_28_hostname_prefix(self):
        assert _blocked("http://172.28.internal.corp/service")

    def test_zero_zero(self):
        assert _blocked("http://0.0.0.0/")

    def test_metadata_google(self):
        assert _blocked("http://metadata.google.internal/computeMetadata/v1/")

    def test_metadata_aws_literal(self):
        # 169.254.169.254 is in _BLOCKED_HOSTNAMES explicitly
        assert _blocked("http://169.254.169.254/")

    def test_dotlocalhost_subdomain(self):
        assert _blocked("http://evil.localhost/")

    def test_no_scheme_bare_ip(self):
        # Scheme is added automatically; still blocked
        assert _blocked("127.0.0.1")

    def test_missing_host(self):
        ok, reason = is_safe_public_target("http:///path")
        assert not ok
        assert reason


# ---------------------------------------------------------------------------
# Should be ALLOWED
# ---------------------------------------------------------------------------

class TestAllowed:
    def test_public_https(self):
        assert _allowed("https://example.com/")

    def test_public_http(self):
        assert _allowed("http://example.com/path?q=1")

    def test_public_ip_cloudflare(self):
        # 1.1.1.1 is a genuine public IP (Cloudflare DNS)
        assert _allowed("https://1.1.1.1/")

    def test_public_ip_8_8_8_8(self):
        assert _allowed("https://8.8.8.8/")

    def test_172_15_is_public(self):
        # 172.15.x.x is NOT in the RFC 1918 range (172.16.0.0/12 starts at 172.16)
        assert _allowed("https://172.15.0.1/")

    def test_172_32_is_public(self):
        # 172.32.x.x is NOT in the RFC 1918 range (172.16.0.0/12 ends at 172.31)
        assert _allowed("https://172.32.0.1/")

    def test_no_scheme_public(self):
        # Scheme is added automatically; public host passes
        assert _allowed("example.com")
