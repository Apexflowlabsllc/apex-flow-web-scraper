"""URL and address checks are repeated by the resolver at connection time."""
import asyncio
import ipaddress
import re
import socket
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from aiohttp.abc import AbstractResolver


class ScrapeError(Exception):
    def __init__(self, code, message, retry_after=None):
        self.code, self.retry_after = code, retry_after
        super().__init__(message)


TRACKING = {"fbclid", "gclid", "msclkid", "mc_cid", "mc_eid"}


def canonical_url(url, strip_tracking=True):
    if not isinstance(url, str) or len(url) > 4096 or re.search(r"[\x00-\x20\\]", url):
        raise ScrapeError("invalid_url", "A valid public http(s) URL is required")
    try:
        p = urlsplit(url)
        if p.scheme.lower() not in {"http", "https"} or not p.hostname or p.username or p.password:
            raise ValueError()
        host = p.hostname.rstrip(".").encode("idna").decode().lower()
        port = p.port
    except (ValueError, UnicodeError):
        raise ScrapeError("invalid_url", "URL must use http(s) without embedded credentials")
    if port not in (None, 80, 443):
        # A separate internal-only fixture policy can allow ephemeral test ports.
        pass
    authority = f"[{host}]" if ":" in host else host
    if port and port != (443 if p.scheme.lower() == "https" else 80):
        authority += f":{port}"
    query = p.query
    if strip_tracking:
        query = urlencode([(k, v) for k, v in parse_qsl(query, keep_blank_values=True)
                           if not k.lower().startswith("utm_") and k.lower() not in TRACKING])
    return urlunsplit((p.scheme.lower(), authority, p.path or "/", query, ""))


def check_address(value, allow_test_loopback=False):
    addr = ipaddress.ip_address(value.split("%")[0])
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    if allow_test_loopback and addr.is_loopback:
        return
    if not addr.is_global or addr.is_multicast or addr.is_reserved:
        raise ScrapeError("blocked_address", "Only public internet addresses are permitted")


def check_url(url, allow_test_loopback=False):
    url = canonical_url(url, strip_tracking=False)
    p = urlsplit(url)
    host = p.hostname
    if p.port not in (None, 80, 443) and not allow_test_loopback:
        raise ScrapeError("blocked_port", "Only public HTTP and HTTPS ports are permitted")
    if host == "localhost" or host.endswith((".local", ".localhost", ".internal")):
        if not allow_test_loopback:
            raise ScrapeError("blocked_address", "Local names are not permitted")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return url
    check_address(host, allow_test_loopback)
    return url


class PublicResolver(AbstractResolver):
    def __init__(self, allow_test_loopback=False):
        self.allow_test_loopback = allow_test_loopback

    async def resolve(self, host, port=0, family=socket.AF_INET):
        results = await asyncio.get_running_loop().getaddrinfo(
            host, port, type=socket.SOCK_STREAM, family=family)
        output = []
        for af, _, proto, _, address in results:
            check_address(address[0], self.allow_test_loopback)
            output.append({"hostname": host, "host": address[0], "port": port,
                           "family": af, "proto": proto, "flags": socket.AI_NUMERICHOST})
        if not output:
            raise ScrapeError("dns_failed", "Host has no usable addresses")
        return output

    async def close(self):
        pass
