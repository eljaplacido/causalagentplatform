"""Download a benchmark file over http(s), and nothing else.

The real-data benchmarks (T2 IHDP, T3 LaLonde) fetch their files from the
`base_url` in their pre-registration and check each against a pinned SHA-256.
They used `urllib.request.urlopen`, which also opens `file://` and any other
scheme a handler is registered for (bandit B310). The URL comes from a
committed file, but a pre-registration is edited by hand, and a `file://`
there would read a local file into the benchmark.

So the opener here is built from the HTTP and HTTPS handlers only: it has no
handler for any other scheme, and urllib's redirect handler already refuses a
redirect to one. The scheme is also checked up front so the error names the URL.
"""

from __future__ import annotations

import urllib.request
from urllib.parse import urlsplit

ALLOWED_SCHEMES = ("http", "https")


def _opener() -> urllib.request.OpenerDirector:
    opener = urllib.request.OpenerDirector()
    for handler in (
        urllib.request.HTTPHandler(),
        urllib.request.HTTPSHandler(),
        urllib.request.HTTPRedirectHandler(),
        urllib.request.HTTPDefaultErrorHandler(),
        urllib.request.HTTPErrorProcessor(),
        urllib.request.UnknownHandler(),  # any other scheme: URLError("unknown url type")
    ):
        opener.add_handler(handler)
    return opener


def download(url: str, timeout: float) -> bytes:
    """GET `url` and return its body. Refuses any scheme but http and https."""
    parts = urlsplit(url)
    if parts.scheme not in ALLOWED_SCHEMES or not parts.netloc:
        raise ValueError(f"benchmark data URL must be http(s) with a host, got {url!r}")
    with _opener().open(url, timeout=timeout) as resp:
        body: bytes = resp.read()
    return body
