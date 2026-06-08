import ipaddress
import re
from urllib.parse import urlparse

_BLOCKED_HOSTNAMES = frozenset(
    {
        "localhost",
        "0.0.0.0",
        "metadata.google.internal",
        "metadata",
        # AWS / GCP / Azure instance metadata endpoints
        "169.254.169.254",
        "fd00:ec2::254",
    }
)

# Matches hostnames that *begin* with a private/reserved IPv4 prefix, e.g.
# "10.internal.host" or "172.20.proxy.local".  Covers all RFC 1918 ranges:
#   10.0.0.0/8      → 10.
#   172.16.0.0/12   → 172.16–172.31 (second octet 16–31)
#   192.168.0.0/16  → 192.168.
# Plus loopback (127.0.0.0/8) and link-local (169.254.0.0/16).
_PRIVATE_PREFIX_RE = re.compile(
    r"^(?:"
    r"10\."
    r"|127\."
    r"|169\.254\."
    r"|192\.168\."
    r"|172\.(?:1[6-9]|2\d|3[01])\."
    r")"
)


def is_safe_public_target(url: str) -> tuple[bool, str]:
    """
    Reject obvious SSRF targets (loopback, RFC1918, link-local, metadata hostnames).
    """
    u = url.strip()
    if not u.startswith(("http://", "https://")):
        u = "https://" + u
    try:
        p = urlparse(u)
    except Exception:
        return False, "Invalid URL"
    host = (p.hostname or "").lower()
    if not host:
        return False, "Missing host"
    if host in _BLOCKED_HOSTNAMES:
        return False, "Host not allowed"
    if host.endswith(".localhost"):
        return False, "Host not allowed"

    # IPv4 literal
    ipv4 = re.fullmatch(r"(\d{1,3}\.){3}\d{1,3}", host)
    if ipv4:
        try:
            ip = ipaddress.ip_address(host)
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
                return False, "IP range not allowed"
        except ValueError:
            return False, "Invalid IP"

    # IPv6 literal
    if host.startswith("["):
        return False, "Bracket hosts not supported"
    if ":" in host and "." not in host.split(":")[0]:
        try:
            ip = ipaddress.ip_address(host)
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
                return False, "IP range not allowed"
        except ValueError:
            pass

    # Block hostnames whose prefix matches a private/reserved IPv4 range
    # (e.g. "172.20.proxy.local" or "10.internal.svc").  _PRIVATE_PREFIX_RE
    # covers the full RFC 1918 space including 172.16–172.31.
    if _PRIVATE_PREFIX_RE.match(host):
        return False, "Host not allowed"

    return True, ""
