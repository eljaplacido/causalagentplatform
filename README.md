# Causal Platform

A governed platform for causal reasoning, decision simulation, and intervention recommendation. Built around a canonical semantic layer, a Guardian-mediated approval workflow, and a shared library of causal-inference skills that are callable both internally (LangGraph agents) and externally (Model Context Protocol).

> **Status:** Pre-1.0. The causal numbers the platform serves come from the causal-core kernel, which identifies before it estimates, refuses with a typed reason, and passes pre-registered correctness and real-data benchmarks. Several platform features are still heuristic (and labelled so) or not built: counterfactuals, discovery, the LLM layer, signed governance verdicts. [`docs/CURRENT_STAGE.md`](docs/CURRENT_STAGE.md) lists what is implemented, each row with the test that pins it, and what is declared but not implemented. `.gnomon/ci.sh` is the gate; `scripts/status_claims.py` keeps that page in step with the code.

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

For agents: [`packages/causal-mcp`](packages/causal-mcp) serves the kernel over MCP (SDK v2, stdio
or stateless HTTP). It has four read-only tools: check a graph, identify, analyze, and verify a
narrative before it reaches a user.

**Evidence, pre-registered before each run.** Each benchmark's thresholds are committed before it
runs, and failing runs are kept.

| Benchmark | Result |
|---|---|
| T0: 29 identification cases, 14 estimation designs, 7 seeded defects ([`benchmarks/t0`](benchmarks/t0)) | Identification agrees with y0 on 200/200 random graphs. Pooled 95% coverage is 94.9%. Every seeded defect is caught. |
| T3: LaLonde, real data with an experimental benchmark ([`benchmarks/t3-lalonde`](benchmarks/t3-lalonde)) | No confident wrong answer. Naive estimates are off by up to −$15,205; every kernel interval covers the experimental $1,794. |
| T2: IHDP-100, semi-synthetic ([`benchmarks/t2-ihdp`](benchmarks/t2-ihdp)) | ATT coverage 0.99 / 0.98; supported results miss the truth 1.5% of the time. |

Known limits are listed, not hidden: [`docs/CURRENT_STAGE.md`](docs/CURRENT_STAGE.md) and
[`conformance/declared_not_implemented.json`](conformance/declared_not_implemented.json).

## What else is in the box

- **Backend** (`backend/`): Python 3.12, FastAPI, SQLAlchemy and LangGraph. `.gnomon/ci.sh` prints the current test count and coverage.
- **Frontend** (`frontend/`): Next.js, TypeScript and Tailwind. Persona-scoped views: workbench, governance, incidents and engineer.
- **Legacy MCP server** (`backend/mcp_server/`): exposes seven backend skills as MCP tools; console script `causal-platform-mcp`. Which of them return real estimates is listed in `docs/CURRENT_STAGE.md`. Prefer `packages/causal-mcp` for estimates.
- **Docs** (`docs/`): architecture (AGENTS.md), data layer, ADRs, quickstart and testing standards.

## Quick start

```bash
# 1. Backend
cd backend
python -m venv .venv
.venv/bin/pip install -e ../packages/causal-core -e ".[dev]"   # the kernel is a local package
.venv/bin/python -m scripts.seed_data --all
.venv/bin/python -m pytest        # full backend suite (coverage is judged by .gnomon/ci.sh)

# 2. Frontend
cd ../frontend
npm install

# 3. Launch (two terminals)
bash launch_backend.sh             # backend on :49234
cd frontend && NEXT_PUBLIC_API_BASE_URL=http://localhost:49234 npm run dev
```

Then open <http://localhost:3000/workbench>.

Container: `docker build -f backend/Dockerfile .` from the repository root.

Full guide: [`docs/QUICKSTART.md`](docs/QUICKSTART.md).

## Calling skills from an external agent

```bash
cd backend
.venv/bin/python -m mcp_server.examples.client_demo
```

Full MCP client setup: [`backend/mcp_server/README.md`](backend/mcp_server/README.md).

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
