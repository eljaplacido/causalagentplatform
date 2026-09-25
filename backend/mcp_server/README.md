# Causal Platform — MCP server (legacy)

> **DEPRECATED.** Use [`packages/causal-mcp`](../../packages/causal-mcp/README.md) (console
> script `causal-mcp`): the kernel's server, with typed structured output, identification
> before estimation, typed refusals, a narrative verifier and, with the `[decision]` extra,
> `causal_decide`. This server (MCP SDK v1) predates the kernel and is kept only until the
> backend is fully rewired onto causal-core. Do not add tools to it.

A [Model Context Protocol](https://modelcontextprotocol.io) server that exposes
the backend's seven causal skills as tools over stdio.

## Tools advertised

| Tool                            | What it does                                             |
| ------------------------------- | -------------------------------------------------------- |
| `causal_effect_estimate`        | ATE for an APPROVED fragment, delegated to causal-core   |
| `causal_identification_check`   | Identification, delegated to causal-core                 |
| `causal_counterfactual_reason`  | Heuristic triage score, not an effect (NOT_ASSESSED)     |
| `causal_sensitivity_analysis`   | E-value / Rosenbaum-bound robustness check               |
| `causal_drift_detect`           | KL divergence between reference vs current data          |
| `causal_intervention_estimate`  | Heuristic triage score for one proposed edge, not an effect |
| `causal_structure_compare`      | Structural drift: edges going stale against fresh data   |

**Recommended call order for an agent acting on data:**
`identification_check` → `effect_estimate` → `sensitivity_analysis`. If
identification returns `UNIDENTIFIED`, do *not* act on the point estimate.
Without enough observational data the estimate tool returns a refusal (for
example `LOW_N`) and no number.

## Install & run

```bash
cd backend
.venv/bin/pip install -e ../packages/causal-core -e ../packages/causal-decision -e .
.venv/bin/causal-platform-mcp       # serve over stdio (console script in backend/pyproject.toml)
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
      "command": "/abs/path/to/backend/.venv/bin/causal-platform-mcp"
    }
  }
}
```

For Claude Desktop, this goes in `claude_desktop_config.json`. The `command`
must be an absolute path — Claude Desktop does not run through your shell, so
`PATH` lookups will fail silently.

## End-to-end example

A runnable client lives at [`examples/client_demo.py`](examples/client_demo.py).
It spawns this legacy server (`python -m mcp_server.server`), lists the
advertised tools, and calls `causal_identification_check` +
`causal_effect_estimate` against a toy fragment.

```bash
cd backend
.venv/bin/python -m mcp_server.examples.client_demo
```

The toy fragment has eight rows, so the estimate is a refusal rather than a
number -- which is the point of the demo as much as the happy path is:

```
── effect_estimate ─────────
... "status": "refusal", ... "ate": null, ... "refusal_reason": "LOW_N",
"detail": "n=8 is below policy.min_n=30", "ok": true
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
are isolated from skill regressions. `tests/integration/test_mcp_stdio.py`
covers the stdio transport itself.
