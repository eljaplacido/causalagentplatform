# Causal Platform

[![CI](https://github.com/eljaplacido/causalagentplatform/actions/workflows/ci.yml/badge.svg)](https://github.com/eljaplacido/causalagentplatform/actions/workflows/ci.yml)

A governed platform for causal reasoning, decision simulation, and intervention recommendation. Built around a canonical semantic layer, a Guardian-mediated approval workflow, and a shared library of causal-inference skills that are callable both internally (LangGraph agents) and externally (Model Context Protocol).

> **Status:** Pre-1.0. The causal numbers the platform serves come from the causal-core kernel, which identifies before it estimates, refuses with a typed reason, and passes pre-registered correctness and real-data benchmarks. On top of it, causal-decision rules on whether an agent may act on that evidence. Several platform features are still heuristic (and labelled so) or not built: counterfactuals, discovery, and calibration evidence for the decisions (the follow-up loop exists; no real operation has been measured with it yet). The LLM layer is optional and local-first (Ollama): it writes explanations only, kept when every number in them is a claim of the kernel's outcome, and the platform runs unchanged without it. [`docs/CURRENT_STAGE.md`](docs/CURRENT_STAGE.md) lists what is implemented, each row with the test that pins it, and what is declared but not implemented. `.gnomon/ci.sh` is the gate (`scripts/verify.sh` runs it; exit 0 means every step ran and passed, 1 that a step failed, 2 that one could not be measured, 3 that a document is owed a re-read); `scripts/status_claims.py` keeps that page in step with the code.

## Start here: the causal kernel

[`packages/causal-core`](packages/causal-core) is the part to use today. It identifies before it
estimates, refuses with a typed reason when the data cannot answer, and binds every number it
returns to the computation that produced it.

```python
import causal_core as cc

out = cc.analyze(df, "Z -> X; Z -> Y; X -> Y", treatment="X", outcome="Y")  # or estimand="att"
out.kind           # "result" | "refusal" | "apparatus_failure"
print(cc.report(out))
```

## Then: may the agent act on it?

[`packages/causal-decision`](packages/causal-decision) is the layer between an agent's proposed
action and the system that action would change. The kernel says what the effect is; this says what
to do about it — **act**, **act with approval**, **run an experiment first**, or **refuse** — from
the risk of being wrong, the smallest effect worth having, and whether the action can be undone.

```python
from causal_decision import DecisionRequest, decide, explain

d = decide(out, DecisionRequest(action="apply remediation playbook X", outcome="minutes",
                                direction="decrease", minimum_worthwhile_effect=5.0))
d.verdict          # act | act_with_approval | experiment | refuse
d.experiment       # when the evidence cannot settle it: the trial that would, sized
print(explain(d, out))
```

It is a ladder, not a score: every check names the strongest verdict it still allows, and the
weakest of those wins, so no amount of good news outvotes one blocking problem. The verdict is a
pure function of (outcome, request, policy) and carries digests of all three, so it can be
replayed and disputed. It executes nothing.

For agents: [`packages/causal-mcp`](packages/causal-mcp) serves the kernel over MCP (SDK v2, stdio
or stateless HTTP). Four read-only tools: check a graph, identify, analyze, and verify a narrative
before it reaches a user — plus two with the `[decision]` extra: `causal_decide`, which analyses and
rules in one call so nothing can change the evidence in between, not even the agent, and
`causal_impact`, which says whether a revised graph breaks an earlier estimate.

**Evidence, pre-registered before each run.** Each benchmark's thresholds are committed before it
runs, and failing runs are kept.

| Benchmark | Result |
|---|---|
| T0: 29 identification cases, 14 estimation designs, 10 seeded defects ([`benchmarks/t0`](benchmarks/t0)) | Identification agrees with y0 on 200/200 random graphs. Pooled 95% coverage is 94.9%. Triangulation makes a result with a misspecified outcome model contested in 100% of runs at n = 2,000, where comparing estimates alone caught 3.5%; false alarms 1.1%. Every seeded defect is caught. |
| T3: LaLonde, real data with an experimental benchmark ([`benchmarks/t3-lalonde`](benchmarks/t3-lalonde)) | No confident wrong answer. Naive estimates are off by up to −$15,205; every kernel interval covers the experimental $1,794. |
| T2: IHDP-100, semi-synthetic ([`benchmarks/t2-ihdp`](benchmarks/t2-ihdp)) | ATT coverage 0.99 / 0.98; supported results miss the truth 1.5% of the time. Against rivals (reported, not gated): OLS with covariates matches the kernel's ATT error (0.247 vs 0.252) but its ATE intervals cover only 73%; the kernel's ATE point error is worse than a naive difference in means (0.88 vs 0.29) — its advantage is honest intervals, not sharper points. |
| T4: segment effects ([`benchmarks/t4-segments`](benchmarks/t4-segments)) | Per-segment coverage 0.93–0.97, family-wise coverage 0.945–0.98, 4% false discovery with every segment null. Bars registered before the code was written. |
| T5: staggered difference-in-differences ([`benchmarks/t5-did`](benchmarks/t5-did)) | Coverage 0.932–0.958 across nine simulated rollouts: one where two-way fixed effects is biased, one where adoption timing follows covariates that also drive trends (the doubly robust estimate is unbiased there; ignoring the covariates is off by 0.54), each nuisance model misspecified in turn, and rollouts that reach every unit, compared with the not-yet-treated. Pre-trends test power 1.00 with false alarms ≤ 2.2%. v1 failed on over-coverage and is kept. |
| T6: anytime-valid monitoring ([`benchmarks/t6-sequential`](benchmarks/t6-sequential)) | A watched canary's confidence sequence contains the effect at every look in 98.2–99.4% of runs; the fixed-n interval re-read at every look manages 72.9%. v1 failed on two faults of its registration and is kept. |
| T7: cluster-randomized designs ([`benchmarks/t7-cluster`](benchmarks/t7-cluster)) | Randomize services, measure requests: coverage 0.942–0.952 across five designs; unit-level standard errors would cover 37%. Where a few huge clusters dominate, the kernel refuses rather than under-cover. v1–v3 failed and are kept. |
| T8: switchback designs ([`benchmarks/t8-switchback`](benchmarks/t8-switchback)) | One system switched hourly between arms: coverage 0.945–0.956 with a washout that covers the carryover. When it does not, a carryover check makes the result contested: in 91.8% of runs when the carryover biases the estimate by three standard errors, but in only 9.5% when the bias is a fraction of one (coverage 0.887) — choose the washout from knowledge of the system. |
| T9: Rosenbaum sensitivity ([`benchmarks/t9-rosenbaum`](benchmarks/t9-rosenbaum)) | How much hidden bias would explain an effect away, on a matched design the kernel builds. With the hidden bias planted, the reported Γ* reached it in 1.9–2.9% of runs (bar 3.5%) and kept the conclusion at two-thirds of it in 91.8–96.7%; behind a full analysis with an unmeasured confounder of odds ratio 3, the median Γ* was 1.63 against a design sensitivity of 1.88, and no run claimed 3. It reproduces Rosenbaum's published table on the lead data. |
| T10: positivity and overlap ([`benchmarks/t10-positivity`](benchmarks/t10-positivity)) | Strata where one arm never occurs, including one three covariates define jointly that every single covariate hides, refused in 99.2–100% of runs (the old check: none); false refusals at most 0.2%; coverage 0.946–0.958 where the kernel answers. Nothing is trimmed silently. IHDP's ATE is now flagged for limited overlap in 100/100 realizations. |
| D0: the decision procedure ([`benchmarks/d0-decision-policy`](benchmarks/d0-decision-policy)) | 22/22 scenarios return the verdict the registration names (two with declared segments, two staggered rollouts, four on who proposed an unreviewed edge); 8 properties hold over 400 seeded cases; each of 9 seeded defects makes the gate fail. |
| A0: whether a revised graph breaks a verdict ([`benchmarks/a0-assumption-impact`](benchmarks/a0-assumption-impact)) | 800/800 revisions judged as an independent linear-Gaussian oracle judges them; 4 seeded defects caught. v1 failed its class-balance floor and is kept. |
| C0: the calibration report, on **simulated** decision streams ([`benchmarks/c0-calibration`](benchmarks/c0-calibration)) | Not a calibration figure about real operations — there are none yet. On streams whose effects are set by construction, the report's intervals cover that truth in 95.75–100% of streams for every design and rate (bar 92.5%), and the exact coverage of its interval is never below 95.0% from 10 to 200 follow-ups. Below 10 it states no rate, and too few disagreeing pairs read "could not resolve", not "no difference". 5 seeded defects caught. |

Known limits are listed, not hidden: [`docs/CURRENT_STAGE.md`](docs/CURRENT_STAGE.md) and
[`conformance/declared_not_implemented.json`](conformance/declared_not_implemented.json).

## What else is in the box

- **Backend** (`backend/`): Python 3.12, FastAPI, SQLAlchemy and LangGraph. `.gnomon/ci.sh` prints the current test count and coverage.
- **Frontend** (`frontend/`): Next.js, TypeScript and Tailwind. `/analyze` asks a causal question of your own data (a CSV, or an example simulated with a known effect): the graph is checked for identification as you write it, the answer shows the estimate, its checks and sensitivity or a refusal with what would help, and the decision can be recorded. The home page also lists what the owner's second brain (agentcenter and its Oracle, when configured) suggests testing, each opening as a prefilled analysis. Persona-scoped views: governance, incidents, engineer, analyst ("Problem triage") and executive; and three analysis views over the kernel's records: `/workbench` (fragments, and proposing one through the governed draft flow, which judges the draft in the domain the drafter classified), `/decisions` (the decision ledger, follow-ups and calibration) and `/registry` (the assumption registry: versions, diffs, impact). One navigation bar above every page links them all. [docs/UIX.md](docs/UIX.md) §2 says what each one does.
- **One MCP server**: `packages/causal-mcp`, above. The backend's legacy server (`backend/mcp_server/`, console script `causal-platform-mcp`) was deleted in September 2026; a client configuration that still launches `causal-platform-mcp` should launch `causal-mcp` instead.
- **Docs** (`docs/`): architecture (AGENTS.md), data layer, ADRs, quickstart and testing standards.

## Quick start

```bash
# 1. Backend: the hash-pinned lock, then the local packages (docs/QUICKSTART.md explains)
python -m venv backend/.venv
backend/.venv/bin/python -m pip install --require-hashes --no-deps -r requirements/backend.txt
backend/.venv/bin/python -m pip install --no-deps --no-build-isolation \
  -e packages/causal-core -e packages/causal-decision -e "backend[dev]"
cd backend
.venv/bin/python -m scripts.seed_data --all
.venv/bin/python -m pytest        # full backend suite (coverage is judged by .gnomon/ci.sh)

# 2. Frontend
cd ../frontend
npm install

# 3. Launch (two terminals, both from the repository root)
bash launch_backend.sh             # backend on :49234
cd frontend && NEXT_PUBLIC_API_BASE_URL=http://localhost:49234 npm run dev
# then open http://localhost:3000/analyze?example=email-campaign for a first analysis
```

Then open <http://localhost:3000/workbench>.

Containers: `docker build -f backend/Dockerfile .` and `docker build -f frontend/Dockerfile .`,
both from the repository root.

Full guide: [`docs/QUICKSTART.md`](docs/QUICKSTART.md).

## Calling the kernel from an external agent

In its own virtualenv (causal-mcp needs MCP SDK v2):

```bash
pip install -e 'packages/causal-core' -e 'packages/causal-decision' -e 'packages/causal-mcp[decision]'
causal-mcp                        # stdio; `causal-mcp --http --port 8765` needs the [http] extra
```

Client configuration and the tool list: [`packages/causal-mcp/README.md`](packages/causal-mcp/README.md).

## Documentation

| File | What it covers |
|---|---|
| [`docs/AGENTS.md`](docs/AGENTS.md) | Agent registry, skill library, `EpistemicState` contract, MCP exposure |
| [`docs/CURRENT_STAGE.md`](docs/CURRENT_STAGE.md) | Status: what is implemented (with its test) and what is declared but not implemented |
| [`docs/ROADMAP.md`](docs/ROADMAP.md) | Where this is going: causal decision assurance for agentic workflows, and the order of work |
| [`docs/DATA_LAYER.md`](docs/DATA_LAYER.md) | Storage, connectors, drift detection |
| [`docs/CYBERSECURITY.md`](docs/CYBERSECURITY.md) | Threat model, auth, compliance |
| [`docs/TESTING_CICD.md`](docs/TESTING_CICD.md) | Test pyramid, quality gates |
| [`docs/QUICKSTART.md`](docs/QUICKSTART.md) | Onboarding |

## Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md). Open work is in the ledger, `.beads/issues.jsonl`; `septacore ready` lists it by priority, and [`docs/ROADMAP.md`](docs/ROADMAP.md) says how it fits the plan. Security issues: [`SECURITY.md`](SECURITY.md). Behaviour: [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md).

## License

Apache 2.0 — see [`LICENSE`](LICENSE) and [`NOTICE`](NOTICE).
