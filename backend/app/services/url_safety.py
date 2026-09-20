"""Minimal SSRF-safe HTTP fetch helper for Updated Job Search source adapters.

Independent of app.agents.job_url_import_agent (which stays untouched — it
serves the separate "import one job by URL" feature). Adapters here only
ever talk to a small, hardcoded set of known public API hosts, so the safety
bar is simpler: HTTP(S) only, no redirects followed, no private/internal
IP addresses.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit


class UnsafeUrlError(ValueError):
    pass


def assert_public_http_url(url: str) -> None:
    parsed = urlsplit((url or "").strip())
    if parsed.scheme.lower() not in ("http", "https"):
        raise UnsafeUrlError("Only HTTP and HTTPS URLs are allowed.")
    if parsed.username or parsed.password:
        raise UnsafeUrlError("URLs containing credentials are not allowed.")
    hostname = (parsed.hostname or "").lower().rstrip(".")
    if not hostname:
        raise UnsafeUrlError("URL must include a hostname.")
    try:
        records = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    except socket.gaierror as error:
        raise UnsafeUrlError("Hostname could not be resolved.") from error
    addresses = {record[4][0].split("%")[0] for record in records}
    if not addresses:
        raise UnsafeUrlError("Hostname could not be resolved.")
    for address in addresses:
        if not ipaddress.ip_address(address).is_global:
            raise UnsafeUrlError("Private or internal network addresses are not allowed.")


def safe_get(session, url: str, **kwargs):
    """GET a public HTTP(S) URL without following redirects (redirect targets
    are not re-validated, so we simply refuse to follow them)."""
    assert_public_http_url(url)
    kwargs.setdefault("allow_redirects", False)
    response = session.get(url, **kwargs)
    if response.is_redirect or response.status_code in (301, 302, 303, 307, 308):
        raise UnsafeUrlError("Redirects are not followed for job source fetches.")
    return response
