"""
Every MCP tool, exercised over the real stdio transport at INFO logging.

The unit tests in ``tests/unit/test_mcp_tools.py`` call the wrappers directly,
which cannot see two transport-level failure classes:

  * anything a skill or library writes to **stdout** corrupts the JSON-RPC
    stream (the client reads stdout as protocol frames);
  * a logger that only misbehaves when a record is actually emitted (e.g. a
    stdlib logger called with structlog-style kwargs) passes at WARNING and
    crashes at INFO.

So this test spawns the server as a subprocess with ``LOG_LEVEL=INFO``, calls
every advertised tool with valid input, and then re-reads the raw stdout bytes:
each line must parse as a JSON-RPC message. The tool set is taken from the
server's own ``tools/list`` and compared with ``CAUSAL_TOOLS`` so a tool added
without a case here fails the test instead of being silently skipped.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest

from mcp_server.tools import CAUSAL_TOOLS

BACKEND = Path(__file__).resolve().parents[2]

FRAGMENT = {
    "approval_status": "APPROVED",
    "edges": [
        {
            "source": {"id": "tx", "object_type": "Treatment"},
            "target": {"id": "oy", "object_type": "Outcome"},
            "confidence": 0.8,
            "edge_type": "CAUSES",
        }
    ],
}
DATA = {
    "tx": [0.0, 1.0, 0.0, 1.0, 0.0, 1.0, 0.0, 1.0],
    "oy": [0.2, 0.9, 0.1, 1.1, 0.3, 1.0, 0.0, 1.2],
}

# One valid call per advertised tool. Keys must equal the server's tool list.
CASES: dict[str, dict[str, Any]] = {
    "causal_effect_estimate": {"fragment": FRAGMENT, "data": DATA},
    "causal_counterfactual_reason": {
        "proposed_edges": FRAGMENT["edges"],
        "query": "What if tx were 1?",
    },
    "causal_identification_check": {"fragment": FRAGMENT},
    "causal_sensitivity_analysis": {"ate": 0.35, "confidence_interval": [0.2, 0.5]},
    "causal_drift_detect": {
        "reference_data": {"f": [0.0, 0.1, 0.2, 0.0, 0.1] * 4},
        "current_data": {"f": [5.0, 5.1, 5.2, 5.0, 5.1] * 4},
    },
    "causal_intervention_estimate": {
        "source_id": "tx",
        "target_id": "oy",
        "edge_type": "CAUSES",
        "confidence": 0.75,
    },
    "causal_structure_compare": {"fragment": FRAGMENT, "data": DATA},
}


async def _drive_server() -> tuple[list[str], dict[str, dict[str, Any]], bytes]:
    """Run the server, list tools, call each one; return (tool names, results, raw stdout)."""
    env = {**os.environ, "LOG_LEVEL": "INFO", "PYTHONUNBUFFERED": "1"}
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "mcp_server.server",
        cwd=BACKEND,
        env=env,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    assert proc.stdin is not None and proc.stdout is not None
    raw = bytearray()
    next_id = 0

    async def request(method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        nonlocal next_id
        next_id += 1
        msg: dict[str, Any] = {"jsonrpc": "2.0", "id": next_id, "method": method}
        if params is not None:
            msg["params"] = params
        proc.stdin.write((json.dumps(msg) + "\n").encode())
        await proc.stdin.drain()
        while True:
            line = await asyncio.wait_for(proc.stdout.readline(), timeout=60)
            if not line:
                raise AssertionError(f"server closed stdout while waiting for {method}")
            raw.extend(line)
            frame = json.loads(line)  # a non-JSON line is exactly the defect under test
            if frame.get("id") == next_id:
                return frame

    try:
        init = await request(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "stdio-test", "version": "0"},
            },
        )
        assert "result" in init, init
        proc.stdin.write(
            (json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n").encode()
        )
        listed = await request("tools/list", {})
        names = sorted(t["name"] for t in listed["result"]["tools"])
        results: dict[str, dict[str, Any]] = {}
        for name in names:
            frame = await request("tools/call", {"name": name, "arguments": CASES.get(name, {})})
            results[name] = frame
    finally:
        proc.stdin.close()
        try:
            await asyncio.wait_for(proc.wait(), timeout=15)
        except TimeoutError:
            proc.kill()
    # Anything the process wrote after the last response still counts.
    raw.extend(await proc.stdout.read())
    return names, results, bytes(raw)


def _payload(frame: dict[str, Any]) -> dict[str, Any]:
    result = frame["result"]
    if result.get("structuredContent") is not None:
        sc = result["structuredContent"]
        return sc.get("result", sc) if isinstance(sc, dict) else sc
    return json.loads(result["content"][0]["text"])


@pytest.fixture(scope="module")
def session() -> tuple[list[str], dict[str, dict[str, Any]], bytes]:
    return asyncio.run(_drive_server())


def test_tool_list_matches_registry_and_cases(session: Any) -> None:
    names, _, _ = session
    assert names == sorted(CAUSAL_TOOLS), "server advertises a different tool set than tools.py"
    assert set(names) == set(CASES), "every advertised tool needs a stdio case in this test"


def test_stdout_carries_only_jsonrpc(session: Any) -> None:
    _, _, raw = session
    lines = [ln for ln in raw.decode().splitlines() if ln.strip()]
    assert lines, "no protocol frames captured"
    for ln in lines:
        frame = json.loads(ln)
        assert frame.get("jsonrpc") == "2.0", f"non-protocol line on stdout: {ln[:200]}"


@pytest.mark.parametrize("tool", sorted(CASES))
def test_every_tool_returns_ok_over_stdio(session: Any, tool: str) -> None:
    _, results, _ = session
    frame = results[tool]
    assert "error" not in frame, frame
    payload = _payload(frame)
    assert payload.get("ok") is True, f"{tool} returned error envelope: {payload}"
