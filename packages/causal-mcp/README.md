# causal-mcp

[causal-core](../causal-core) as a Model Context Protocol server (MCP Python SDK v2). Any MCP
client or agent gets causal inference that identifies before it estimates, refuses when it cannot,
and binds every number it returns to a claim.

```json
{ "mcpServers": { "causal": { "command": "causal-mcp" } } }
```

```bash
pip install causal-mcp                 # the four evidence tools
pip install 'causal-mcp[decision]'     # adds causal_decide and causal_impact (installs causal-decision)
pip install 'causal-mcp[http]'         # adds the HTTP transport (uvicorn)

causal-mcp                        # stdio (default)
causal-mcp --http --port 8765     # stateless streamable HTTP; needs the [http] extra
```

## Tools

Four tools are always served, and two more, `causal_decide` and `causal_impact`, when the
`[decision]` extra is installed (the server checks at import and registers them only then). Every tool is annotated
read-only and idempotent, and all return typed structured output. Output schemas carry structure
only (no descriptions, titles or defaults), which keeps the tool listing under the 64 KiB line limit of stdio clients;
the full contract, prose included, is served as the `causal://schemas/*` resources. The `outputSchema` is the causal
claim contract, published under `conformance/causal-claims/`.

| Tool | Use it to |
|---|---|
| `causal_check_graph` | Draft a causal graph with a user. It validates acyclicity, lists unreviewed (for example LLM-proposed) edges, and lists every testable conditional independence the graph implies. |
| `causal_identify` | Decide *before collecting data* whether the effect is identifiable, and how: back-door, front-door, IV, proximal or general ID. It validates a user-chosen adjustment set. |
| `causal_analyze` | Estimate the effect, or receive a typed refusal (`NON_IDENTIFIABLE`, `POSITIVITY`, `WEAK_INSTRUMENT`, …) together with what information would help. |
| `causal_verify_narrative` | Check prose before it reaches a user. Every number must be a claim from the outcome, and causal wording is allowed only for a `supported` result. |
| `causal_decide` | *(`[decision]` extra.)* Decide whether a proposed action is justified: analyses and applies the decision policy in one call, so the evidence cannot change between estimate and verdict. Returns one of `act`, `act_with_approval`, `experiment` or `refuse`, the outcome it rested on, and an explanation whose numbers are claims. |
| `causal_impact` | *(`[decision]` extra.)* Does a revised graph break an earlier answer? Give the old and new graph and the earlier query: the adjustment set its analysis used, and its `design`, `instruments` and proxies; returns `holds`, `invalidated` or `unverified` (or `gained` / `still_unidentified`) with the edge-level changes. Checked by the pre-registered A0 gate in `packages/causal-decision`. |

**Recommended flow**, as the server's instructions give it: `causal_check_graph` →
`causal_identify` → `causal_analyze` → `causal_verify_narrative`, or `causal_decide` in place of
the last two when the question is whether to *do* something. A refusal is a correct answer, not an
error: relay its `missing_information`. The verdict is the tool's, not the calling agent's.

**Using `causal_decide`.** State what acting is worth before asking: `minimum_worthwhile_effect`
(the smallest benefit, in outcome units, that justifies the action), `direction` (`increase` or
`decrease`), `risk_class` (`high` always goes to a person) and `reversible`. Declare `segment_by`
columns before seeing any answer; the verdict will not automate an action that materially harms a
declared subgroup. When the evidence cannot settle the question, the response carries the
experiment that would, sized from the data. The verdict is a pure function of (outcome, request,
policy) and carries the digests of all three.

**Parameters.** The analysis parameters (`design`, `estimand`, `adjust_for`, `segment_by`,
`cluster`, `washout`, `sequence`, `unit`, `time`, ...) are not copied into the tools: they are
the fields of `causal_core.contract.CausalQuery`, published in each tool's input schema with the
contract's type, default, bounds and description (`src/causal_mcp/query_params.py`). A field the
kernel gains reaches `causal_analyze` and `causal_decide` without an edit here. `causal_identify`
and `causal_impact` take the fields identification reads. `rung` is not served: the kernel
answers interventional questions only. Bounds are enforced by the server, so a value outside
them (`expected_treated_share` of 1, a negative `washout`) is a tool error before the tool runs.

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
- `tests/test_tools.py` calls the tool functions directly: bad input and a `data_path` outside
  the roots are refusals, an invalid adjustment set comes back with a valid one, and declared
  segments are estimated.
- `tests/test_decision_tool.py` covers `causal_decide`: a strong effect on a reversible action is
  acted on, high risk reaches a person on the same evidence, an unidentified effect yields the
  experiment that would settle it, bad input is a refusal and never an action, declared segments
  reach the verdict, the server registers the tool read-only, `causal_impact` re-certifies a
  randomized or proximal answer by its own strategy, and `causal_decide` takes treatment levels.
  It skips when causal-decision is not installed.
- `tests/test_query_parity.py` walks `CausalQuery.model_fields` against each tool's input schema
  as the server lists it, and fails naming the tool and the field when one is missing or differs
  in type, default, bounds or description. Negative controls: a contract grown by a field, and
  one with a reworded description, a changed default and a changed type, each fail it.
