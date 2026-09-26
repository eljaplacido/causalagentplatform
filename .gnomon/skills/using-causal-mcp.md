+++
name = "using the causal-mcp tools"
description = "The kernel's MCP flow (check_graph, identify, analyze or decide, verify_narrative), how to pass data, and what to do with a refusal"
match = '\b(causal|effect|estimate|estimat\w*|ate|att|confound\w*|identif\w*|graph|dag|decide|decision|verdict|refusal|mcp|causal_\w+)\b'
+++

**The server is `mcp__causal_core__*`** (packages/causal-mcp, declared as
`causal_core` in tools.toml), the repository's one MCP server. (A legacy
backend server, `mcp__causal__*`, was deleted in septa-6; if a client still
offers its tools, it is running an old checkout -- do not cite it.)

**The flow, in order.** It is the server's own INSTRUCTIONS
(packages/causal-mcp/src/causal_mcp/server.py):

1. `causal_check_graph` -- draft the graph with the user. Read
   `testable_implications` (each is a claim of an absent edge; test them on
   data before trusting the graph) and `unreviewed_edges`.
2. `causal_identify` -- is the effect identifiable from the graph alone,
   before any data? A user-chosen `adjust_for` is validated here.
3. `causal_analyze` -- estimate, or receive a typed refusal.
   OR `causal_decide` when the question is whether to *do* something: it
   analyses and rules in one call, so the evidence cannot change between the
   estimate and the verdict. It exists only when causal-mcp is installed with
   the `[decision]` extra; if it is missing from your tool list, say so rather
   than improvising a verdict from `causal_analyze`.
   When the graph behind an earlier answer changes, call `causal_impact` with
   the old graph, the new graph and the earlier query: the adjustment set its
   analysis used (its certificate's `adjustment_set`), and its `design`,
   `instruments` and proxies, so its own strategy is the one re-certified.
   "Still back-door" is not "still valid"; only `holds` means the earlier
   answer stands.
4. `causal_verify_narrative` -- pass your prose and the `outcome` object
   unchanged before replying. Every number must be a claim from the outcome;
   causal wording ("caused", "will reduce") is allowed only for a `supported`
   result. It fails closed.

**Parameters come from the kernel's contract.** Every analysis parameter
(`design`, `estimand`, `segment_by`, `cluster`, `washout`, `sequence`, `unit`,
`time`, `covariates`, `control_group`, ...) is a field of `causal_core.contract.CausalQuery`, published in
the tool's input schema with the contract's type, default, bounds and
description (packages/causal-mcp/src/causal_mcp/query_params.py). Read the
description there rather than guessing a value. `causal_analyze` and
`causal_decide` take every field but `rung` (the kernel answers interventional
questions only); `causal_identify` and `causal_impact` take the fields
identification reads. A bound violated over MCP (`expected_treated_share`
outside (0, 1), a negative `washout`) is rejected by the server before the
tool runs, as a tool error rather than a refusal. In a switchback (`cluster` the
time block, with `time` and `washout`), an effect still moving after the washout
makes the result `contested`; the check misses a carryover that is small next to
the standard error, so the washout still comes from knowledge of the system.

Graph syntax: `Z -> X -> Y; Z -> Y`; `X <-> Y` is an unmeasured common cause;
`latent U` declares an unmeasured node. A graph node with no data column is
treated as latent.

**Data.** Exactly one of `data` (`{column: [values]}` inline) or `data_path`
(CSV, Parquet, JSON/JSONL). A `data_path` must resolve under a directory in
`CAUSAL_MCP_DATA_ROOTS` (`os.pathsep`-separated); unset, the only root is the
server's working directory -- here, the directory gnomon was started from
(the tools.toml invocation does not `cd`). A path outside the roots comes back as an `INVALID_INPUT` refusal, not
as an error.

**A refusal is a result.** `NON_IDENTIFIABLE`, `POSITIVITY`,
`WEAK_INSTRUMENT`, `NO_ESTIMATOR`, `RUNG_MISMATCH`, `INVALID_INPUT` ... are
correct answers. Report the reason and relay `missing_information`. Do not
work around a refusal: no hand-computed difference in means, no re-run with a
smaller adjustment set chosen to make it go through, no estimate "for
reference".

**The verdict is the tool's.** `causal_decide` returns one of `act`,
`act_with_approval`, `experiment`, `refuse`. Relay its `explanation` -- its
numbers are bound to the analysis -- rather than writing your own, and never
upgrade `experiment` to `act` on your reading of the interval. Declare
`minimum_worthwhile_effect`, `direction`, `risk_class` and any `segment_by`
columns BEFORE seeing an answer; choosing them after is the researcher degree
of freedom the tool exists to remove.

**Segments.** `segment_by` names discrete pre-treatment columns, declared up
front. Quote the family-wise interval for any claim about a segment, and if
`heterogeneity.differs` is false, say the segments could not be told apart.
The kernel will not search for the best subgroup, deliberately.

**Report the labels with the number**: `estimator`, the certificate's
strategy, sensitivity labels, and the call that produced it.
