# Causal Platform

[![CI](https://github.com/eljaplacido/causalagentplatform/actions/workflows/ci.yml/badge.svg)](https://github.com/eljaplacido/causalagentplatform/actions/workflows/ci.yml)

A governed platform for causal reasoning, decision simulation, and intervention recommendation. Built around a canonical semantic layer, a Guardian-mediated approval workflow, and a shared library of causal-inference skills that are callable both internally (LangGraph agents) and externally (Model Context Protocol).

> **Status:** Pre-1.0. The causal numbers the platform serves come from the causal-core kernel, which identifies before it estimates, refuses with a typed reason, and passes pre-registered correctness and real-data benchmarks. On top of it, causal-decision rules on whether an agent may act on that evidence. Several platform features are still heuristic (and labelled so) or not built: counterfactuals, discovery, the LLM layer, and calibration evidence for the decisions (the follow-up loop exists; no real operation has been measured with it yet). [`docs/CURRENT_STAGE.md`](docs/CURRENT_STAGE.md) lists what is implemented, each row with the test that pins it, and what is declared but not implemented. `.gnomon/ci.sh` is the gate; `scripts/status_claims.py` keeps that page in step with the code.

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
| T0: 29 identification cases, 14 estimation designs, 7 seeded defects ([`benchmarks/t0`](benchmarks/t0)) | Identification agrees with y0 on 200/200 random graphs. Pooled 95% coverage is 94.9%. Every seeded defect is caught. |
| T3: LaLonde, real data with an experimental benchmark ([`benchmarks/t3-lalonde`](benchmarks/t3-lalonde)) | No confident wrong answer. Naive estimates are off by up to −$15,205; every kernel interval covers the experimental $1,794. |
| T2: IHDP-100, semi-synthetic ([`benchmarks/t2-ihdp`](benchmarks/t2-ihdp)) | ATT coverage 0.99 / 0.98; supported results miss the truth 1.5% of the time. Against rivals (reported, not gated): OLS with covariates matches the kernel's ATT error (0.247 vs 0.252) but its ATE intervals cover only 73%; the kernel's ATE point error is worse than a naive difference in means (0.88 vs 0.29) — its advantage is honest intervals, not sharper points. |
| T4: segment effects ([`benchmarks/t4-segments`](benchmarks/t4-segments)) | Per-segment coverage 0.93–0.97, family-wise coverage 0.945–0.98, 4% false discovery with every segment null. Bars registered before the code was written. |
| T5: staggered difference-in-differences ([`benchmarks/t5-did`](benchmarks/t5-did)) | Coverage 0.938–0.958 across four simulated rollouts, including one where two-way fixed effects is biased; pre-trends test power 1.00 with false alarms ≤ 2.2%. v1 failed on over-coverage and is kept. |
| T6: anytime-valid monitoring ([`benchmarks/t6-sequential`](benchmarks/t6-sequential)) | A watched canary's confidence sequence contains the effect at every look in 98.2–99.4% of runs; the fixed-n interval re-read at every look manages 72.9%. v1 failed on two faults of its registration and is kept. |
| T7: cluster-randomized designs ([`benchmarks/t7-cluster`](benchmarks/t7-cluster)) | Randomize services, measure requests: coverage 0.942–0.952 across five designs; unit-level standard errors would cover 37%. Where a few huge clusters dominate, the kernel refuses rather than under-cover. v1–v3 failed and are kept. |
| D0: the decision procedure ([`benchmarks/d0-decision-policy`](benchmarks/d0-decision-policy)) | 19/19 scenarios return the verdict the registration names (two with declared segments, two staggered rollouts); 8 properties hold over 400 seeded cases; each of 8 seeded defects makes the gate fail. |
| A0: whether a revised graph breaks a verdict ([`benchmarks/a0-assumption-impact`](benchmarks/a0-assumption-impact)) | 800/800 revisions judged as an independent linear-Gaussian oracle judges them; 4 seeded defects caught. v1 failed its class-balance floor and is kept. |

Known limits are listed, not hidden: [`docs/CURRENT_STAGE.md`](docs/CURRENT_STAGE.md) and
[`conformance/declared_not_implemented.json`](conformance/declared_not_implemented.json).

## What else is in the box

- **Backend** (`backend/`): Python 3.12, FastAPI, SQLAlchemy and LangGraph. `.gnomon/ci.sh` prints the current test count and coverage.
- **Frontend** (`frontend/`): Next.js, TypeScript and Tailwind. Persona-scoped views: workbench, governance, incidents, engineer, analyst and executive.
- **Legacy MCP server** (`backend/mcp_server/`): **deprecated** — it warns on startup, and no gnomon role is granted it. It exposes seven backend skills (console script `causal-platform-mcp`) and is kept only for client configurations that still launch it; its retirement is tracked as `legacy-mcp-server` in `conformance/declared_not_implemented.json`. Use `packages/causal-mcp`.
- **Docs** (`docs/`): architecture (AGENTS.md), data layer, ADRs, quickstart and testing standards.

## Quick start

```bash
# 1. Backend
cd backend
python -m venv .venv
.venv/bin/pip install -e ../packages/causal-core -e ../packages/causal-decision -e ".[dev]"   # local packages
.venv/bin/python -m scripts.seed_data --all
.venv/bin/python -m pytest        # full backend suite (coverage is judged by .gnomon/ci.sh)

# 2. Frontend
cd ../frontend
npm install

# 3. Launch (two terminals, both from the repository root)
bash launch_backend.sh             # backend on :49234
cd frontend && NEXT_PUBLIC_API_BASE_URL=http://localhost:49234 npm run dev
```

Then open <http://localhost:3000/workbench>.

Container: `docker build -f backend/Dockerfile .` from the repository root.

Full guide: [`docs/QUICKSTART.md`](docs/QUICKSTART.md).

## Calling the kernel from an external agent

In its own virtualenv — the backend pins MCP SDK v1 for the legacy server, causal-mcp needs v2:

```bash
pip install -e 'packages/causal-core' -e 'packages/causal-decision' -e 'packages/causal-mcp[decision]'
causal-mcp                        # stdio; `causal-mcp --http --port 8765` needs the [http] extra
```

Client configuration and the tool list: [`packages/causal-mcp/README.md`](packages/causal-mcp/README.md).
The legacy server's docs remain at [`backend/mcp_server/README.md`](backend/mcp_server/README.md).

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

See [`CONTRIBUTING.md`](CONTRIBUTING.md). Security issues: [`SECURITY.md`](SECURITY.md). Behaviour: [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md).

## License

Apache 2.0 — see [`LICENSE`](LICENSE) and [`NOTICE`](NOTICE).
