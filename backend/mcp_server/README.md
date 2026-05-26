# Causal Platform — MCP server

A [Model Context Protocol](https://modelcontextprotocol.io) server that exposes
the platform's seven causal-inference skills as tools. Any MCP-aware client
(Claude Desktop, custom agents, IDE plugins) can call them over stdio.

The intent: give external agents the ability to **reason about cause and
effect** instead of just correlation, with the same identifiability and
sensitivity guardrails the platform applies internally.

## Tools advertised

| Tool                            | What it does                                             |
| ------------------------------- | -------------------------------------------------------- |
| `causal_effect_estimate`        | ATE for an APPROVED graph fragment (DoWhy-backed)        |
| `causal_identification_check`   | IDENTIFIED / PARTIALLY_IDENTIFIED / UNIDENTIFIED verdict |
| `causal_counterfactual_reason`  | "What if" queries over proposed edges                    |
| `causal_sensitivity_analysis`   | E-value / Rosenbaum-bound robustness check               |
| `causal_drift_detect`           | KL divergence between reference vs current data          |
| `causal_intervention_estimate`  | Risk-tiered effect for a single proposed edge            |
| `causal_structure_compare`      | Structural drift: edges going stale against fresh data   |

**Recommended call order for an agent acting on data:**
`identification_check` → `effect_estimate` → `sensitivity_analysis`. If
identification returns `UNIDENTIFIED`, do *not* act on the point estimate.

## Install & run

```bash
cd backend
.venv/bin/pip install -e .          # picks up the `mcp` dep + console script
.venv/bin/causal-mcp                # serve over stdio
# or equivalently
.venv/bin/python -m mcp_server.server
```

The server speaks MCP over stdio and writes logs to stderr.

## Connecting an MCP client

Drop this into the client's MCP-server config (paths vary by client):

```json
{
  "mcpServers": {
    "causal-platform": {
      "command": "/abs/path/to/backend/.venv/bin/causal-mcp"
    }
  }
}
```

For Claude Desktop, this goes in `claude_desktop_config.json`. The `command`
must be an absolute path — Claude Desktop does not run through your shell, so
`PATH` lookups will fail silently.

## End-to-end example

A runnable client lives at [`examples/client_demo.py`](examples/client_demo.py).
It spawns the server, lists the advertised tools, and calls
`causal_identification_check` + `causal_effect_estimate` against a toy
fragment.

```bash
cd backend
.venv/bin/python -m mcp_server.examples.client_demo
```

Expected tail of the output (ATE will vary on real data):

```
── effect_estimate ─────────
[ { "text": "{\"ate\": 0.9, \"estimator\": \"dowhy.backdoor\", \"ok\": true, ...}" } ]
```

## Calling tools from Python directly (no stdio)

The wrappers in `tools.py` are plain async functions — useful in tests or
in-process agents:

```python
from mcp_server.tools import tool_effect_estimate

result = await tool_effect_estimate(
    fragment={"approval_status": "APPROVED", "edges": [...]},
    data={"tx": [...], "oy": [...]},
)
```

> **Watch out:** the `effect_estimate` skill truncates IDs to 8 characters
> when matching against DataFrame columns. Keep `source.id` / `target.id`
> ≤8 chars (e.g. `tx`, `oy`) or the column lookup will silently mismatch.

## Error contract

Every tool returns JSON-native dicts only. On failure, wrappers return a
structured envelope instead of raising:

```json
{
  "ok": false,
  "error": "Estimation failed: ...",
  "error_type": "EffectEstimationError",
  "tool": "effect_estimate"
}
```

A calling agent should always get a parseable result — never an unhandled
exception across the transport.

## Tests

```bash
cd backend
.venv/bin/python -m pytest tests/unit/test_mcp_tools.py --no-cov -v
```

The tests call the wrappers in-process (no stdio), so transport regressions
are isolated from skill regressions.
