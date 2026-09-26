+++
name = "adding an estimator or causal capability"
description = "Kernel first: where a new causal method goes, and the four things it must ship with (typed contract, influence-function SEs, a pre-registered gate, a seeded defect)"
match = '\b(estimator|estimators|aipw|ipw|dml|tmle|iv|instrument\w*|front[- ]?door|back[- ]?door|proximal|sensitivity|refut\w*|skill|skills|capability|new method|causal-core|causal_core|kernel)\b'
roles = ["coordinator", "implementor", "implement", "plan", "critique"]
+++

**Kernel first.** A new causal capability goes into `packages/causal-core`
(estimation, identification, refutation, sensitivity) or
`packages/causal-decision` (anything that turns evidence into a verdict).
It does NOT go into `backend/src/skills/` -- that directory is not to grow.
The backend's `effect_estimate` and `identification_check` skills already
delegate to the kernel (`backend/src/skills/_kernel.py`); a backend feature
that needs a new method calls the kernel through that route. The agent-facing
surface is `packages/causal-mcp`, the one MCP server; the backend serves
no MCP tools.

**An estimator is a plug-in** satisfying the `Estimator` protocol in
`causal_core/estimators/__init__.py`: `name`, `family`, the `strategies` and
`treatment_kinds` it handles, a `priority`, and `fit(d, policy, rng) -> Fit`.
Register it with `register(...)` in-tree, or from another package through the
`causal_core.estimators` entry point. The pipeline runs every estimator that
fits the certificate and triangulates them, so a second estimator from a
different `family` is itself a check on the first.

**What it ships with -- all four, in this order:**

1. **A typed contract.** Its output is an `EstimateRecord` inside the existing
   `CausalResult`, or a new field/kind in `causal_core/contract.py`. A contract
   change bumps `CONTRACT_VERSION` and regenerates the published schemas
   (`causal-core schema --out conformance/causal-claims/`);
   `test_committed_schemas_match_the_code` fails until you do. Anything it
   cannot estimate is a typed `Abstention` with a reason, never a
   best-effort number.
2. **Influence-function standard errors.** `Fit.influence` carries the
   per-observation influence function of the point estimate (already scaled);
   the interval is derived from it, not from a bootstrap chosen because it
   was easier, and never from a constant.
3. **A pre-registered benchmark.** Write `benchmarks/<gate>/PRE-REGISTRATION.md`
   with its TOML bars block and COMMIT IT before the capability produces a
   single number (see the benchmark-discipline skill). Simulated cases with a
   known truth go in `causal_core/bench/`; the gate test reads its bars from
   the TOML block and gets its own pytest marker.
4. **A seeded-defect negative control.** A plausible wrong implementation,
   as a context manager next to the others (`causal_core/bench/defects.py`,
   `defects_t4.py`), registered in the pre-registration's `[seeded_defects]`
   with the bar that must catch it. A test asserts the gate FAILS under each
   defect. A gate nobody has seen fail proves nothing.

**Then keep the claims true.** If the method closes a register entry, move
it to `left_the_list` (see register-hygiene). If the kernel README or
`docs/CURRENT_STAGE.md` lists it as not implemented, update that line in the
same change. Surface it through causal-mcp only after the gate passes.

**Done** is `.gnomon/ci.sh` green, with the new suite counted in the
`causal-core` (or `causal-decision`) row -- not a passing local pytest.
