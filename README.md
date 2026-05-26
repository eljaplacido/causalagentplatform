# Causal Platform

A governed platform for causal reasoning, decision simulation, and intervention recommendation. Built around a canonical semantic layer, a Guardian-mediated approval workflow, and a shared library of causal-inference skills that are callable both internally (LangGraph agents) and externally (Model Context Protocol).

> **Status:** Pre-1.0. Phase 1 (foundation) complete; Phase 2 (DAG Workbench) underway. See [`docs/CURRENT_STAGE.md`](docs/CURRENT_STAGE.md) for the live roadmap.

## What's in the box

- **Backend** (`backend/`) — Python 3.12 + FastAPI + SQLAlchemy + LangGraph. 551 passing tests.
- **Frontend** (`frontend/`) — Next.js + TypeScript + Tailwind. Persona-scoped views: workbench, governance, incidents, engineer.
- **MCP server** (`backend/mcp_server/`) — exposes seven causal skills as MCP tools for any external agent. Console script: `causal-mcp`.
- **Docs** (`docs/`) — architecture (AGENTS.md), data layer, ADRs, quickstart, testing standards.

## Quick start

```bash
# 1. Backend
cd backend
python -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/python -m scripts.seed_data --all
.venv/bin/python -m pytest        # 551 passing

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
| [`docs/CURRENT_STAGE.md`](docs/CURRENT_STAGE.md) | Roadmap, phase completion, active work streams |
| [`docs/DATA_LAYER.md`](docs/DATA_LAYER.md) | Storage, connectors, drift detection |
| [`docs/CYBERSECURITY.md`](docs/CYBERSECURITY.md) | Threat model, auth, compliance |
| [`docs/TESTING_CICD.md`](docs/TESTING_CICD.md) | Test pyramid, quality gates |
| [`docs/QUICKSTART.md`](docs/QUICKSTART.md) | Onboarding |

## Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md). Security issues: [`SECURITY.md`](SECURITY.md). Behaviour: [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md).

## License

Apache 2.0 — see [`LICENSE`](LICENSE) and [`NOTICE`](NOTICE).
