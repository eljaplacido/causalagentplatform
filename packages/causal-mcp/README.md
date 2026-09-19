# causal-mcp

[causal-core](../causal-core) as a Model Context Protocol server (MCP Python SDK v2). Any MCP
client or agent gets causal inference that identifies before it estimates, refuses when it cannot,
and binds every number it returns to a claim.

```json
{ "mcpServers": { "causal": { "command": "causal-mcp" } } }
```

```bash
causal-mcp                        # stdio (default)
causal-mcp --http --port 8765     # stateless streamable HTTP; needs the [http] extra
```

## Tools

All four tools are annotated read-only and idempotent, and all return typed structured output. The
`outputSchema` is the causal claim contract, published under `conformance/causal-claims/`.

| Tool | Use it to |
|---|---|
| `causal_check_graph` | Draft a causal graph with a user. It validates acyclicity, lists unreviewed (for example LLM-proposed) edges, and lists every testable conditional independence the graph implies. |
| `causal_identify` | Decide *before collecting data* whether the effect is identifiable, and how: back-door, front-door, IV, proximal or general ID. It validates a user-chosen adjustment set. |
| `causal_analyze` | Estimate the effect, or receive a typed refusal (`NON_IDENTIFIABLE`, `POSITIVITY`, `WEAK_INSTRUMENT`, …) together with what information would help. |
| `causal_verify_narrative` | Check prose before it reaches a user. Every number must be a claim from the outcome, and causal wording is allowed only for a `supported` result. |

The contract's JSON Schemas are also served as resources at `causal://schemas/<name>`.

**Data.** Pass it inline as `{"column": [values, …]}`, or as `data_path`. A `data_path` must lie
under a directory listed in `CAUSAL_MCP_DATA_ROOTS` (`os.pathsep`-separated); the default is the
server's working directory. Paths outside those roots are refused.

**What is deliberately not a tool.** Anything that approves, signs or acts. An agent must never be
able to grant itself an approval. Governance lives in the platform's policy layer, not in an agent's
toolbox.

## Evidence

- `tests/test_stdio_flow.py` runs the recommended agent flow over the real stdio transport at INFO
  logging: check graph → identify → analyze → refusal → verify. It asserts that every stdout line
  is a JSON-RPC frame and that each outcome validates against the published schema.
- `tests/test_http.py` runs the same tools over stateless HTTP.
