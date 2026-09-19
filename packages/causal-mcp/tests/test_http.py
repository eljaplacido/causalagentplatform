"""Stateless streamable HTTP: the same tools, one POST per call, no session state."""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import time
from typing import Any

import pytest

httpx = pytest.importorskip("httpx")
pytest.importorskip("uvicorn")

HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
    "MCP-Protocol-Version": "2025-06-18",
}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _post(url: str, body: dict[str, Any]) -> dict[str, Any]:
    r = httpx.post(url, json=body, headers=HEADERS, timeout=30)
    r.raise_for_status()
    if r.headers.get("content-type", "").startswith("text/event-stream"):
        data = [ln[5:].strip() for ln in r.text.splitlines() if ln.startswith("data:")]
        return json.loads(data[-1])  # type: ignore[no-any-return]
    return r.json()  # type: ignore[no-any-return]


def test_stateless_http_round_trip() -> None:
    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", "causal_mcp.server", "--http", "--port", str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    url = f"http://127.0.0.1:{port}/mcp"
    try:
        init = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "t", "version": "0"},
            },
        }
        for _ in range(80):
            try:
                assert "result" in _post(url, init)
                break
            except (httpx.TransportError, httpx.HTTPStatusError):
                time.sleep(0.25)
        else:
            pytest.fail("HTTP server did not come up")
        call = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "causal_identify",
                "arguments": {"graph": "X -> M -> Y; X <-> Y", "treatment": "X", "outcome": "Y"},
            },
        }
        cert = _post(url, call)["result"]["structuredContent"]["certificate"]
        assert cert["strategy"] == "frontdoor" and cert["mediators"] == ["M"]
    finally:
        proc.terminate()
        proc.wait(timeout=10)
