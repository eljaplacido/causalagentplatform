"""The benchmark downloader speaks http(s) only (bandit B310).

The real-data benchmarks used urllib.request.urlopen on the pre-registration's
base_url; urlopen also opens file:// and custom schemes. causal_core.bench._fetch
refuses them up front, and its opener has no handler for them either, so a
file:// URL cannot be read even past the check.
"""

from __future__ import annotations

import http.server
import threading
import urllib.error
from collections.abc import Iterator
from pathlib import Path

import pytest

from causal_core.bench import _fetch

BODY = b"known bytes \x00\x01 for the benchmark downloader"


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/data.bin":
            self.send_response(200)
            self.send_header("Content-Length", str(len(BODY)))
            self.end_headers()
            self.wfile.write(BODY)
        elif self.path == "/to-file":
            self.send_response(302)
            self.send_header("Location", "file:///etc/hostname")
            self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args: object) -> None:
        pass


@pytest.fixture()
def server() -> Iterator[str]:
    httpd = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_downloads_the_bytes_served_over_http(server: str) -> None:
    assert _fetch.download(f"{server}/data.bin", timeout=5) == BODY


def test_an_http_error_is_raised_not_returned(server: str) -> None:
    with pytest.raises(urllib.error.HTTPError):
        _fetch.download(f"{server}/missing", timeout=5)


def test_a_redirect_to_file_is_refused(server: str) -> None:
    with pytest.raises(urllib.error.HTTPError):
        _fetch.download(f"{server}/to-file", timeout=5)


@pytest.mark.parametrize(
    "url", ["file:///etc/passwd", "ftp://example.org/x", "data:,hello", "example.org/x", "http://"]
)
def test_any_other_scheme_is_refused(url: str) -> None:
    with pytest.raises(ValueError, match="must be http"):
        _fetch.download(url, timeout=5)


def test_the_opener_itself_cannot_open_a_file_url(tmp_path: Path) -> None:
    # Past the scheme check too: the opener has no FileHandler, so even a direct
    # open of a file:// URL (a real file, here) fails rather than reading it.
    secret = tmp_path / "secret.txt"
    secret.write_bytes(b"local file contents")
    with pytest.raises(urllib.error.URLError, match="unknown url type"):
        _fetch._opener().open(secret.as_uri(), timeout=5)
