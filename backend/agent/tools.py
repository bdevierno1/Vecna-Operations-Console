"""Passive recon tools for the Vecna security agent (custom implementations)."""

from __future__ import annotations

import json
import re
import socket
from contextvars import ContextVar
from urllib.parse import urlparse

from strands import tool

from agent.tool_hooks import run_tool_with_hooks
from app.services.http_client import recon_client
from app.url_guard import is_safe_public_target

# Filled by the operation runner so tool outputs feed the live findings panel.
_findings_bucket: ContextVar[list[dict] | None] = ContextVar("vecna_findings", default=None)


def set_findings_bucket(bucket: list[dict] | None) -> None:
    _findings_bucket.set(bucket)


def _merge_findings(items: list[dict]) -> None:
    b = _findings_bucket.get()
    if b is None:
        return
    for f in items:
        if isinstance(f, dict) and f.get("title"):
            b.append(f)


# Small passive wordlist — HEAD/GET only, no brute forcing
DEFAULT_PATHS = (
    "/robots.txt",
    "/.well-known/security.txt",
    "/.well-known/change-password",
    "/sitemap.xml",
    "/.git/config",
    "/admin",
    "/api",
    "/health",
    "/status",
)


def _normalize_base(url: str) -> str:
    u = url.strip()
    if not u.startswith(("http://", "https://")):
        u = "https://" + u
    parsed = urlparse(u)
    if not parsed.netloc:
        raise ValueError("Invalid URL: missing host")
    scheme = parsed.scheme or "https"
    return f"{scheme}://{parsed.netloc}"


def _host_from_url(url: str) -> str:
    p = urlparse(url if url.startswith("http") else "https://" + url)
    return p.hostname or ""


def _resolve_dns_impl(target_url: str) -> str:
    host = _host_from_url(target_url)
    if not host:
        return json.dumps({"error": "Could not parse host", "host": ""})

    addrs: list[dict[str, str]] = []
    try:
        for family, _, _, _, sockaddr in socket.getaddrinfo(host, None):
            ip = sockaddr[0]
            fam = "IPv6" if family == socket.AF_INET6 else "IPv4"
            addrs.append({"family": fam, "address": ip})
    except OSError as e:
        addrs = [{"error": str(e)}]

    subdomains: list[str] = []
    # crt.sh wildcard query for registered names under domain
    domain = ".".join(host.split(".")[-2:]) if host.count(".") >= 1 else host
    try:
        with recon_client(read_timeout=30.0) as client:
            r = client.get(f"https://crt.sh/?q=%25.{domain}&output=json")
            if r.status_code == 200:
                data = r.json()
                if isinstance(data, list):
                    names = {row.get("name_value", "") for row in data if isinstance(row, dict)}
                    subdomains = sorted({n.strip() for n in names if n})[:200]
    except Exception as e:
        subdomains = [f"crt.sh_error: {e!s}"]

    dns_findings: list[dict[str, str]] = [
        {
            "severity": "info",
            "category": "dns",
            "title": "DNS and certificate transparency",
            "detail": f"Resolved {len(addrs)} address(es); crt.sh returned {len(subdomains)} name(s) for the zone.",
        }
    ]
    _merge_findings(dns_findings)

    return json.dumps(
        {
            "host": host,
            "dns_resolved": addrs[:20],
            "subdomains_sample": subdomains[:50],
            "subdomain_count": len(subdomains),
        },
        indent=2,
    )


@tool
def resolve_dns_and_subdomains(target_url: str) -> str:
    """
    Resolve DNS A/AAAA records for the target host and list certificate transparency
    subdomains from crt.sh (passive, public API). No port scanning.

    Args:
        target_url: Full URL or hostname (e.g. https://example.com/path).

    Returns:
        JSON string with hostname, resolved addresses, and unique subdomains from crt.sh.
    """
    try:
        return run_tool_with_hooks("resolve_dns_and_subdomains", _resolve_dns_impl, target_url)
    except Exception as e:
        return json.dumps({"error": str(e)})


_SECURITY_HEADERS = (
    "strict-transport-security",
    "content-security-policy",
    "x-frame-options",
    "x-content-type-options",
    "referrer-policy",
    "permissions-policy",
)


def _analyze_headers_impl(target_url: str) -> str:
    ok, reason = is_safe_public_target(target_url)
    if not ok:
        return json.dumps({"error": f"URL blocked by SSRF guard: {reason}", "findings": []})
    base = _normalize_base(target_url)
    findings: list[dict[str, str]] = []
    with recon_client(read_timeout=25.0) as client:
        r = client.get(base + "/")
    headers = {k.lower(): v for k, v in r.headers.items()}
    present = {h: headers.get(h) for h in _SECURITY_HEADERS}

    if not present.get("strict-transport-security"):
        findings.append(
            {
                "severity": "medium",
                "category": "headers",
                "title": "Missing HSTS",
                "detail": "No Strict-Transport-Security header on initial response.",
            }
        )
    if not present.get("content-security-policy"):
        findings.append(
            {
                "severity": "medium",
                "category": "headers",
                "title": "Missing CSP",
                "detail": "No Content-Security-Policy header.",
            }
        )
    if not present.get("x-frame-options") and "frame-ancestors" not in (present.get("content-security-policy") or ""):
        findings.append(
            {
                "severity": "low",
                "category": "headers",
                "title": "Clickjacking risk",
                "detail": "No X-Frame-Options and no frame-ancestors in CSP.",
            }
        )
    if not present.get("x-content-type-options"):
        findings.append(
            {
                "severity": "low",
                "category": "headers",
                "title": "Missing X-Content-Type-Options",
                "detail": "Consider nosniff.",
            }
        )

    _merge_findings(findings)

    return json.dumps(
        {
            "final_url": str(r.url),
            "status_code": r.status_code,
            "headers_checked": present,
            "findings": findings,
        },
        indent=2,
    )


@tool
def analyze_http_security_headers(target_url: str) -> str:
    """
    Fetch the target over HTTPS (or HTTP if given) and evaluate security-related response headers.
    Flags missing HSTS, CSP, X-Frame-Options, etc. Passive GET only.

    Args:
        target_url: URL to request (scheme required or https assumed).

    Returns:
        JSON string with status, selected headers, and findings list.
    """
    try:
        return run_tool_with_hooks("analyze_http_security_headers", _analyze_headers_impl, target_url)
    except Exception as e:
        return json.dumps({"error": str(e), "findings": []})


def _probe_paths_impl(target_url: str) -> str:
    ok, reason = is_safe_public_target(target_url)
    if not ok:
        return json.dumps({"error": f"URL blocked by SSRF guard: {reason}", "paths": [], "findings": []})
    base = _normalize_base(target_url).rstrip("/")
    results: list[dict] = []
    findings: list[dict[str, str]] = []
    with recon_client(read_timeout=20.0, follow_redirects=False) as client:
        for path in DEFAULT_PATHS:
            url = base + path
            try:
                resp = client.get(url)
                code = resp.status_code
                snippet = (resp.text or "")[:200].replace("\n", " ")
                results.append({"path": path, "status": code, "length": len(resp.content), "preview": snippet})
                if path == "/.git/config" and code == 200 and "repositoryformatversion" in (resp.text or "").lower():
                    findings.append(
                        {
                            "severity": "critical",
                            "category": "paths",
                            "title": "Possible .git exposure",
                            "detail": f"GET {path} returned 200 with git-config-like body.",
                        }
                    )
                if path == "/robots.txt" and code == 200 and re.search(r"Disallow:\s*/", resp.text or "", re.I):
                    findings.append(
                        {
                            "severity": "info",
                            "category": "paths",
                            "title": "robots.txt disallows paths",
                            "detail": "Review Disallow rules for hidden areas.",
                        }
                    )
            except Exception as ex:
                results.append({"path": path, "error": str(ex)})

    _merge_findings(findings)

    return json.dumps({"paths": results, "findings": findings}, indent=2)


@tool
def probe_common_paths(target_url: str) -> str:
    """
    Request a small fixed list of common paths (robots.txt, .well-known, etc.) using GET.
    Reports HTTP status and whether the path looks exposed. Passive, bounded list only.

    Args:
        target_url: Base site URL.

    Returns:
        JSON with per-path status and findings for sensitive-looking exposures.
    """
    try:
        return run_tool_with_hooks("probe_common_paths", _probe_paths_impl, target_url)
    except Exception as e:
        return json.dumps({"error": str(e), "paths": [], "findings": []})
