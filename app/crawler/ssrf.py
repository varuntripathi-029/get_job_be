"""SSRF guards for user-submitted URLs.

Users can submit arbitrary URLs, and the crawler fetches them from inside our
network. Every URL is validated twice — once at submission and again at crawl
time — because DNS can change in between (a name that resolved publicly when
submitted can later point at 10.x).
"""

import ipaddress
import logging
import socket
from urllib.parse import urlparse, urlunparse

from app.common.exceptions import SSRFError

logger = logging.getLogger(__name__)

ALLOWED_SCHEMES = ("http", "https")
MAX_REDIRECTS = 3

# Cloud instance-metadata endpoints. Link-local covers .254 already, but these
# are called out so the failure message is unambiguous.
BLOCKED_HOSTS = {
    "metadata.google.internal",
    "metadata.goog",
    "instance-data",
}

BLOCKED_IPV4_NETWORKS = [
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("100.64.0.0/10"),  # CGNAT
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),  # link-local, incl. 169.254.169.254
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.0.0.0/24"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("198.18.0.0/15"),  # benchmarking
    ipaddress.ip_network("224.0.0.0/4"),  # multicast
    ipaddress.ip_network("240.0.0.0/4"),  # reserved
]

BLOCKED_IPV6_NETWORKS = [
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("::/128"),
    ipaddress.ip_network("fc00::/7"),  # unique local
    ipaddress.ip_network("fe80::/10"),  # link-local
    ipaddress.ip_network("ff00::/8"),  # multicast
]


def is_blocked_ip(ip: str) -> bool:
    """True if an IP literal falls in a range we refuse to fetch."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return True

    # IPv4-mapped IPv6 (::ffff:10.0.0.1) would otherwise slip past the v4 checks.
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
        addr = addr.ipv4_mapped

    if addr.is_private or addr.is_loopback or addr.is_link_local:
        return True
    if addr.is_reserved or addr.is_multicast or addr.is_unspecified:
        return True

    networks = (
        BLOCKED_IPV4_NETWORKS
        if isinstance(addr, ipaddress.IPv4Address)
        else BLOCKED_IPV6_NETWORKS
    )
    return any(addr in net for net in networks)


class HostResolutionError(SSRFError):
    """DNS did not answer for this host.

    A subclass of SSRFError so submission-time validation still rejects it, but
    distinguishable at crawl time — a host that does not resolve is dead or the
    resolver is down, not an attempt to reach an internal address. The crawler
    retries these with backoff instead of disabling the source outright.
    """

    error_code = "HOST_UNRESOLVABLE"


def resolve_host(hostname: str) -> list[str]:
    """Resolve a hostname to every A/AAAA record it advertises."""
    try:
        infos = socket.getaddrinfo(hostname, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise HostResolutionError(f"Could not resolve host {hostname!r}.") from exc

    addresses = {info[4][0] for info in infos}
    if not addresses:
        raise HostResolutionError(f"Host {hostname!r} resolved to no addresses.")
    return sorted(addresses)


def validate_url(url: str) -> str:
    """Validate and normalize a URL, or raise SSRFError.

    Every resolved address must be public — a hostname with one public and one
    private record is rejected, since we cannot control which the HTTP client picks.
    """
    if not url or not url.strip():
        raise SSRFError("URL is empty.")

    parsed = urlparse(url.strip())

    if parsed.scheme.lower() not in ALLOWED_SCHEMES:
        raise SSRFError(
            f"Scheme {parsed.scheme!r} is not allowed; use http or https."
        )
    if not parsed.hostname:
        raise SSRFError("URL has no host.")

    hostname = parsed.hostname.lower().rstrip(".")

    if hostname in BLOCKED_HOSTS:
        raise SSRFError(f"Host {hostname!r} is blocked.")
    if hostname == "localhost" or hostname.endswith(".localhost"):
        raise SSRFError("Refusing to fetch localhost.")
    if hostname.endswith(".internal") or hostname.endswith(".local"):
        raise SSRFError(f"Host {hostname!r} is in a private namespace.")

    for address in resolve_host(hostname):
        if is_blocked_ip(address):
            raise SSRFError(
                f"Host {hostname!r} resolves to non-public address {address}."
            )

    normalized = parsed._replace(
        scheme=parsed.scheme.lower(), netloc=parsed.netloc.lower(), fragment=""
    )
    return urlunparse(normalized)


def validate_url_after_redirect(url: str, resolved_ip: str | None = None) -> str:
    """Re-validate after a redirect.

    A public URL can 302 to an internal one, so the destination gets the same
    treatment as the original. When the client already knows the peer address,
    pass it as `resolved_ip` to check exactly what was connected to rather than
    re-resolving (which is racy).
    """
    if resolved_ip is not None and is_blocked_ip(resolved_ip):
        raise SSRFError(
            f"Redirect target resolved to non-public address {resolved_ip}."
        )
    return validate_url(url)


def is_safe_url(url: str) -> bool:
    """Non-raising form, for filtering lists."""
    try:
        validate_url(url)
    except SSRFError:
        return False
    return True
