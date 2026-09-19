# Quickstart — Unified Enterprise Causal Platform

> **Target:** New developers and AI agents onboarding to the gx10 satellite.
> **Assumption:** You are on Linux (gx10) with Python 3.12+, Node 20+, and Docker available.

---

## 1. Prerequisites

| Tool | Minimum Version | Verify |
|---|---|---|
| Python | 3.12 | `python3 --version` |
| Node.js | 20.x | `node --version` |
| npm | 10.x | `npm --version` |
| Docker + Compose | 24.x | `docker compose version` |

---

## 2. Repository Setup

```bash
cd /home/eljaplacido/Desktop/causalplatform   # or your clone path
```

### Backend virtual environment

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate          # gx10 uses bash, not Windows
pip install -e ../packages/causal-core -e ".[dev]"   # the causal kernel is a local package
cd ..
```

> **Note:** The repo already has a populated `.venv`. If it ever breaks, delete it and rerun the commands above.

### Frontend dependencies

```bash
# From repo ROOT (not frontend/)
npm ci
```

> **Windows cross-build warning:** If `node_modules` was previously installed from Windows (NTFS), binary symlinks and execute bits may be corrupted. If `npm run type-check` fails with "Permission denied" or missing binaries, purge and reinstall:
> ```bash
> rm -rf node_modules frontend/node_modules packages/*/node_modules
> npm ci
> ```

---

## 3. Configuration

### Backend environment

```bash
cp backend/.env.example backend/.env
```

Edit `backend/.env` and set at minimum:

```bash
ENV=local
DEV_AUTH_BYPASS=true

# LLM provider (choose one)
# For cloud inference:
ANTHROPIC_API_KEY=sk-ant-...
# DEEPSEEK_API_KEY=sk-...
#
# For gx10 local inference (Ollama / OpenFang):
# OLLAMA_BASE_URL=http://127.0.0.1:11434
# OPENFANG_BASE_URL=http://127.0.0.1:4200

HALLUCINATION_RISK_THRESHOLD=0.7
MAX_AGENT_CYCLES=5
```

> **Security:** Never commit `.env` files. They are already gitignored.

### Frontend environment

The frontend reads from `frontend/.env.local` (optional). Defaults are sufficient for local dev.

```bash
echo 'NEXT_PUBLIC_API_BASE_URL=http://localhost:8000' > frontend/.env.local
```

---

## 4. Start Infrastructure (Docker)

The platform requires PostgreSQL 16, Neo4j 5, Kafka (KRaft), and Redis.

```bash
docker compose up -d
```

Services available:

| Service | URL / Port | Notes |
|---|---|---|
| PostgreSQL | `localhost:5432` | User: `causal` / Pass: `causal_local_dev` |
| Neo4j Browser | `http://localhost:7474` | Bolt: `localhost:7687` |
| Kafka | `localhost:9092` | KRaft mode (no Zookeeper) |
| Kafka UI | `http://localhost:8080` | Dev convenience |
| Redis | `localhost:6379` | Session cache |

Stop with:

```bash
docker compose down
```

> **Local dev shortcut:** The backend can also run with **SQLite** (no Docker required). Leave `DATABASE_URL` unset in `backend/.env` and the factory auto-detects SQLite.

---

## 5. Start the Application

### Option A: One-shot launcher (`start.sh`)

```bash
./start.sh            # Starts backend + frontend in background
./start.sh backend    # Backend only
./start.sh frontend   # Frontend only
./start.sh test       # Run backend tests
./start.sh e2e        # Run Playwright E2E tests (requires running stack)
```

### Option B: Manual (two terminals)

**Terminal 1 — Backend:**

```bash
cd backend
.venv/bin/uvicorn src.api.main:app --reload --port 8000 --host 0.0.0.0
```

**Terminal 2 — Frontend:**

```bash
cd frontend
npm run dev
```

---

## 6. Verify Everything Works

| Check | Command | Expected |
|---|---|---|
| Backend health | `curl http://localhost:8000/health` | `{"status":"ok"}` |
| API docs | Open `http://localhost:8000/docs` | Swagger UI |
| Frontend | Open `http://localhost:3000` | Next.js dev server |
| Backend tests | `cd backend && .venv/bin/python -m pytest tests/ -v` | 510+ passed, 80%+ coverage |
| Frontend type-check | `npm run type-check` | No errors |
| Frontend unit tests | `cd frontend && npm test` | 27 passed |

---

## 7. Common gx10 Issues

### `python: command not found`
Use `python3` or activate the venv:
```bash
cd backend && source .venv/bin/activate
```

### `pytest` not found outside venv
Always use the venv binary:
```bash
cd backend && .venv/bin/python -m pytest tests/
```

### `tsc: Permission denied` (frontend)
`node_modules` was copied from Windows. Fix:
```bash
rm -rf node_modules frontend/node_modules packages/*/node_modules
npm ci
```

### Docker services won't start
Ensure ports 5432, 7474, 7687, 9092, 8080, 6379 are free:
```bash
lsof -i :5432 || echo "Port 5432 is free"
```

### Drift tests fail with `scipy` errors
SciPy must be installed in the venv (it's now in `pyproject.toml`):
```bash
cd backend && .venv/bin/pip install scipy>=1.11.0
```

---

## 8. Seed Test Data

Seed simulated data for the four use cases:

```bash
cd backend

# Seed DevOps domain (service entities + graph fragments)
.venv/bin/python -m scripts.seed_data --devops

# Seed Marketing domain (customer churn model)
.venv/bin/python -m scripts.seed_data --marketing

# Seed evaluation metrics (30 days of audit data)
.venv/bin/python -m scripts.seed_data --evaluation

# Seed everything
.venv/bin/python -m scripts.seed_data --all

# Clear seeded data
.venv/bin/python -m scripts.seed_data --clear
```

Test the outcome feedback loop:

```bash
curl -X POST http://localhost:8000/api/v1/interventions/feedback \
  -H "Authorization: Bearer test-token" \
  -H "Content-Type: application/json" \
  -d '{"intervention_id": "<id>", "actual_effect": 0.45}'

curl http://localhost:8000/api/v1/interventions/accuracy \
  -H "Authorization: Bearer test-token"
```

---

## 9. Project Layout

```
causalplatform/
├── backend/                 # FastAPI + LangGraph + agents
│   ├── src/
│   │   ├── agents/          # 9 platform agents
│   │   ├── api/             # REST routers
│   │   ├── db/              # CloudSqlFactory + Neo4jFactory
│   │   ├── skills/          # 12 causal skills
│   │   └── semantic_layer/  # Pydantic models + registry
│   ├── tests/
│   │   ├── unit/            # 400+ unit tests
│   │   └── integration/     # API integration tests
│   └── pyproject.toml       # deps, ruff, mypy, pytest config
├── frontend/                # Next.js 15 + React 19
│   ├── src/app/             # Persona pages (analyst, engineer, ...)
│   ├── src/components/      # Shared + domain components
│   └── package.json
├── docs/                    # Canonical platform docs
│   ├── AGENTS.md            # Agent registry + EpistemicState
│   ├── DATA_LAYER.md        # Semantic layer spec
│   ├── CYBERSECURITY.md     # Threat model + compliance
│   ├── UIX.md               # Personas + user stories
│   └── CURRENT_STAGE.md     # Phase tracking
├── prompts/                 # 12 versioned skill prompts
├── docker-compose.yml       # Local infra stack
└── start.sh                 # Convenience launcher
```

---

## 9. Next Steps

1. **Read the docs** — Start with `docs/AGENTS.md` for the EpistemicState contract and `docs/CURRENT_STAGE.md` for active work streams.
2. **Run the test suite** — `cd backend && .venv/bin/python -m pytest tests/ -v`
3. **Seed test data** — `.venv/bin/python -m scripts.seed_data --all`
4. **Explore the REST API** — `http://localhost:8000/docs` after starting the backend.
5. **Try the MCP skill surface** — From `backend/`, run `.venv/bin/python -m mcp_server.examples.client_demo` to spawn the `causal-mcp` server and call two causal skills end-to-end. See `mcp_server/README.md` for the full MCP client setup.
6. **Load the `herm` skill** — All platform work should load the `herm` skill first (`@herm` in OpenCode) to ensure governance and audit alignment.

---

## 10. Support

- **Platform contracts:** `docs/AGENTS.md`
- **Security boundaries:** `docs/CYBERSECURITY.md`
- **Skill definitions:** `docs/AGENTS.md` §3
- **Current priorities:** `docs/CURRENT_STAGE.md`
