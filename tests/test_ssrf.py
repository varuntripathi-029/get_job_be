"""SSRF guard unit tests. No network or database needed."""

import pytest

from app.common.exceptions import SSRFError
from app.crawler.ssrf import is_blocked_ip, validate_url


@pytest.mark.parametrize(
    "ip",
    [
        "127.0.0.1",
        "10.1.2.3",
        "172.16.0.1",
        "192.168.1.1",
        "169.254.169.254",  # cloud metadata
        "::1",
        "fc00::1",
        "fe80::1",
        "::ffff:10.0.0.1",  # IPv4-mapped IPv6
        "0.0.0.0",
        "not-an-ip",
    ],
)
def test_blocked_ips(ip: str) -> None:
    assert is_blocked_ip(ip) is True


@pytest.mark.parametrize("ip", ["8.8.8.8", "1.1.1.1", "93.184.216.34", "2606:4700::1"])
def test_allowed_ips(ip: str) -> None:
    assert is_blocked_ip(ip) is False


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://example.com",
        "gopher://example.com",
        "http://localhost/admin",
        "http://foo.localhost/",
        "https://metadata.google.internal/",
        "http://service.internal/",
        "http://printer.local/",
        "",
        "   ",
        "https://",
    ],
)
def test_rejected_urls(url: str) -> None:
    with pytest.raises(SSRFError):
        validate_url(url)


def test_normalizes_scheme_host_and_strips_fragment() -> None:
    assert validate_url("HTTPS://Example.COM/Path#frag") == "https://example.com/Path"
