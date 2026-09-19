# Causal Platform

A governed platform for causal reasoning, decision simulation, and intervention recommendation. Built around a canonical semantic layer, a Guardian-mediated approval workflow, and a shared library of causal-inference skills that are callable both internally (LangGraph agents) and externally (Model Context Protocol).

> **Status:** Pre-1.0, and most of the causal layer the platform serves is not real yet. In the backend skills and MCP tools, DoWhy's backdoor and IV point estimates are the only causal numbers a real estimator computes, and no test checks them against a known effect. Confidence intervals, identification, counterfactuals and discovery are heuristics or constants, and no LLM is called. A standalone kernel, `packages/causal-core`, does identification, honest intervals and typed refusals and passes its pre-registered T0 correctness gate; `packages/causal-mcp` serves it over MCP, but nothing in the backend calls it yet. [`docs/CURRENT_STAGE.md`](docs/CURRENT_STAGE.md) lists what is implemented, each row with the test that pins it, and what is declared but not implemented. `.gnomon/ci.sh` is the gate; `scripts/status_claims.py` keeps that page in step with the code.

## What's in the box

- **Backend** (`backend/`) — Python 3.12 + FastAPI + SQLAlchemy + LangGraph. `.gnomon/ci.sh` prints the current test count and coverage.
- **Frontend** (`frontend/`) — Next.js + TypeScript + Tailwind. Persona-scoped views: workbench, governance, incidents, engineer.
- **MCP server** (`backend/mcp_server/`) — exposes seven causal skills as MCP tools for any external agent. Console script: `causal-mcp`. Which of those tools return real estimates is listed in `docs/CURRENT_STAGE.md`.
- **Docs** (`docs/`) — architecture (AGENTS.md), data layer, ADRs, quickstart, testing standards.

## Quick start

```bash
# 1. Backend
cd backend
python -m venv .venv
.venv/bin/pip install -e ".[dev]"
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
| [`docs/DATA_LAYER.md`](docs/DATA_LAYER.md) | Storage, connectors, drift detection |
| [`docs/CYBERSECURITY.md`](docs/CYBERSECURITY.md) | Threat model, auth, compliance |
| [`docs/TESTING_CICD.md`](docs/TESTING_CICD.md) | Test pyramid, quality gates |
| [`docs/QUICKSTART.md`](docs/QUICKSTART.md) | Onboarding |

## Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md). Security issues: [`SECURITY.md`](SECURITY.md). Behaviour: [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md).

## License

Apache 2.0 — see [`LICENSE`](LICENSE) and [`NOTICE`](NOTICE).
