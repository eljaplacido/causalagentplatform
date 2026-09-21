"""
The recommended agent flow, over the real stdio transport, at INFO logging:
check_graph -> identify -> analyze -> verify_narrative, plus a refusal.

Speaks raw JSON-RPC so the test does not depend on the SDK's client, and
re-reads every stdout line: anything that is not a JSON-RPC frame fails.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

import jsonschema
import numpy as np
import pytest
from causal_core.contract import CONTRACT_VERSION

from causal_mcp.server import TOOLS

REPO = Path(__file__).resolve().parents[3]
OUTCOME_SCHEMA = json.loads(
    (REPO / "conformance" / "causal-claims" / CONTRACT_VERSION / "outcome.schema.json").read_text()
)
GRAPH = "Z -> X; Z -> Y; X -> Y"


def _data() -> dict[str, list[float]]:
    rng = np.random.default_rng(0)
    n = 800
    z = rng.normal(size=n)
    x = (0.8 * z + rng.logistic(size=n) > 0).astype(float)
    y = 2.0 * x + 1.2 * z + rng.normal(size=n)
    return {"Z": z.tolist(), "X": x.tolist(), "Y": y.tolist()}


async def _session(calls: list[tuple[str, dict[str, Any]]]) -> tuple[list[dict[str, Any]], bytes]:
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "causal_mcp.server",
        env={**os.environ, "LOG_LEVEL": "INFO", "PYTHONUNBUFFERED": "1"},
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    assert proc.stdin and proc.stdout
    raw = bytearray()
    ids = 0

    async def rpc(method: str, params: dict[str, Any]) -> dict[str, Any]:
        nonlocal ids
        ids += 1
        proc.stdin.write(
            (
                json.dumps({"jsonrpc": "2.0", "id": ids, "method": method, "params": params}) + "\n"
            ).encode()
        )
        await proc.stdin.drain()
        while True:
            line = await asyncio.wait_for(proc.stdout.readline(), timeout=120)
            assert line, f"server closed stdout during {method}"
            raw.extend(line)
            frame = json.loads(line)
            if frame.get("id") == ids:
                return frame

    try:
        init = await rpc(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "t", "version": "0"},
            },
        )
        assert "result" in init, init
        proc.stdin.write(b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
        frames = [await rpc("tools/list", {})]
        for name, args in calls:
            frames.append(await rpc("tools/call", {"name": name, "arguments": args}))
    finally:
        proc.stdin.close()
        try:
            await asyncio.wait_for(proc.wait(), timeout=20)
        except TimeoutError:
            proc.kill()
    raw.extend(await proc.stdout.read())
    return frames, bytes(raw)


def _structured(frame: dict[str, Any]) -> dict[str, Any]:
    assert "error" not in frame, frame
    res = frame["result"]
    assert not res.get("isError"), res
    sc: dict[str, Any] = res["structuredContent"]
    return sc


@pytest.fixture(scope="module")
def flow() -> tuple[list[dict[str, Any]], bytes]:
    data = _data()
    calls = [
        ("causal_check_graph", {"graph": GRAPH}),
        ("causal_identify", {"graph": GRAPH, "treatment": "X", "outcome": "Y"}),
        ("causal_analyze", {"graph": GRAPH, "treatment": "X", "outcome": "Y", "data": data}),
        (
            "causal_analyze",
            {"graph": "X -> Y; X <-> Y", "treatment": "X", "outcome": "Y", "data": data},
        ),
    ]
    frames, raw = asyncio.run(_session(calls))
    result = _structured(frames[3])["outcome"]
    verify_calls = [
        ("causal_verify_narrative", {"text": "X raises Y by ⟦claim:effect⟧.", "outcome": result}),
        ("causal_verify_narrative", {"text": "X causes a 9.99 point rise.", "outcome": result}),
    ]
    frames2, raw2 = asyncio.run(_session(verify_calls))
    return frames + frames2[1:], raw + raw2


def test_tools_advertised_with_output_schemas_and_read_only(flow: Any) -> None:
    frames, _ = flow
    tools = {t["name"]: t for t in frames[0]["result"]["tools"]}
    assert set(tools) == set(TOOLS)
    for t in tools.values():
        assert t.get("outputSchema"), t["name"]
        assert t["annotations"]["readOnlyHint"] is True


def test_stdout_is_only_jsonrpc(flow: Any) -> None:
    _, raw = flow
    for line in raw.decode().splitlines():
        if line.strip():
            assert json.loads(line).get("jsonrpc") == "2.0", line[:200]


def test_graph_and_identification(flow: Any) -> None:
    frames, _ = flow
    check = _structured(frames[1])
    assert check["valid"] and check["observed"] == ["X", "Y", "Z"]
    ident = _structured(frames[2])
    assert ident["identified"] and ident["certificate"]["adjustment_set"] == ["Z"]


def test_analyze_result_and_refusal_match_the_contract(flow: Any) -> None:
    frames, _ = flow
    result = _structured(frames[3])
    refusal = _structured(frames[4])
    for resp in (result, refusal):
        jsonschema.validate(resp["outcome"], OUTCOME_SCHEMA)
    assert result["outcome"]["kind"] == "result"
    ci = result["outcome"]["primary"]["ci"]
    assert ci["lo"] < 2.0 < ci["hi"]
    assert refusal["outcome"]["kind"] == "refusal"
    assert refusal["outcome"]["reason"] == "NON_IDENTIFIABLE"
    assert refusal["report"].startswith("No estimate")


def test_verify_narrative(flow: Any) -> None:
    frames, _ = flow
    good, bad = _structured(frames[5]), _structured(frames[6])
    assert good["ok"] and "⟦" not in good["rendered"]
    assert not bad["ok"] and bad["unbound_numbers"] == ["9.99"]
