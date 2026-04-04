import ipaddress
import re
from urllib.parse import urlparse

_BLOCKED_HOSTNAMES = frozenset(
    {
        "localhost",
        "0.0.0.0",
        "metadata.google.internal",
        "metadata",
    }
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

    # Block common private IPv4 prefixes by label (hostname won't match, but catch dotted names)
    for prefix in ("10.", "192.168.", "127.", "169.254.", "172.16.", "172.17.", "172.18.", "172.19."):
        if host.startswith(prefix):
            return False, "Host not allowed"

    return True, ""
