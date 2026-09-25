# Quickstart — Causal Platform

> **Target:** developers and coding agents setting up a local checkout.
> **Assumption:** Linux or macOS with Python 3.12+ and Node 20+. Docker is optional.
> Coding agents: read the root [`AGENTS.md`](../AGENTS.md) first.

---

## 1. Prerequisites

| Tool | Minimum version | Verify |
|---|---|---|
| Python | 3.12 | `python3 --version` |
| Node.js | 20.x | `node --version` |
| npm | 10.x | `npm --version` |
| Docker + Compose (optional) | 24.x | `docker compose version` |

---

## 2. Repository setup

```bash
cd /path/to/causalplatform   # your clone
```

### Backend virtual environment

The backend depends on the causal kernel and the decision layer, which are local packages:

```bash
cd backend
python3 -m venv .venv
.venv/bin/pip install -e ../packages/causal-core -e ../packages/causal-decision -e ".[dev]"
cd ..
```

### Kernel virtual environment

`packages/causal-core`, `packages/causal-decision` and `packages/causal-mcp` share one environment
of their own (causal-mcp is on MCP SDK v2, the backend's legacy server on v1):

```bash
python3 -m venv packages/causal-core/.venv
packages/causal-core/.venv/bin/pip install \
  -e "packages/causal-core[dev,crosscheck,ml]" \
  -e "packages/causal-decision[dev]" \
  -e "packages/causal-mcp[dev,http,decision]" httpx
```

### Frontend dependencies

```bash
# From the repo ROOT (npm workspaces), not frontend/
npm ci
```

> If `node_modules` was copied from another OS and `npm run type-check` fails with "Permission
> denied" or missing binaries, purge and reinstall:
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

The backend loads `backend/.env` itself at startup (`src/env_bootstrap.py`); variables already in
the environment win. The minimum for local development:

```bash
ENV=local
DEV_AUTH_BYPASS=true
```

**Database engine.** `ENV` decides it, not `DATABASE_URL` (`backend/src/db/factory.py`):

| `ENV` | Engine |
|---|---|
| `local`, or unset | SQLite via aiosqlite, at `SQLITE_PATH` (default `backend/local_dev.db`). `DATABASE_URL` is ignored. |
| anything else | PostgreSQL via asyncpg: the Cloud SQL socket when `CLOUD_SQL_INSTANCE_CONNECTION_NAME` is set, otherwise `DATABASE_URL`. Startup fails if neither is set. |

The dev auth bypass works only with `ENV=local`, so a local session with the bypass is always on
SQLite. Neo4j is used only when `NEO4J_URI` is set; otherwise the graph and experience-buffer paths
fall back to SQL.

**Guardian signing key.** Every Guardian verdict is HMAC-signed with `GUARDIAN_SIGNING_KEY`.
Locally you may leave it unset: each backend process then generates its own key and logs
`guardian.signing_key.ephemeral` at CRITICAL, and its verdicts do not survive a restart. To keep
them across restarts, generate one and put it in `backend/.env` (gitignored):

```bash
echo "GUARDIAN_SIGNING_KEY=$(python3 -c 'import secrets; print(secrets.token_hex(32))')" >> backend/.env
```

With any `ENV` other than `local` the backend **refuses to start** without a key of at least 32
bytes. There, prefer a mounted secret file over a variable: set `GUARDIAN_SIGNING_KEY_FILE` to its
path (a Docker or Kubernetes secret, or a secret manager's file mount). Every secret the backend
reads accepts the same `<NAME>_FILE` form (`DATABASE_URL`, `NEO4J_PASSWORD`, the LLM API keys);
setting both `NAME` and `NAME_FILE` is an error. To rotate the key, list the old one in
`GUARDIAN_SIGNING_KEYS_PREVIOUS` (comma- or newline-separated, or `_FILE`), set the new one and
restart; unspent verdicts signed with the old key keep working until you remove it from the list.
`docs/CYBERSECURITY.md` §8.2 has the details.

**LLM providers.** `backend/.env.example` lists them, and `src/agents/llm_factory.py` can build a
client for each, but no agent or skill calls it today (`llm-routing-no-callers` in the register).
Nothing needs an API key to run.

> **Security:** never commit `.env` files. They are gitignored.

### Frontend environment

`frontend/.env.local` is optional. To point the UI at a backend on a non-default port, and to have
it send the dev bypass token:

```bash
cat > frontend/.env.local <<'EOF'
NEXT_PUBLIC_API_BASE_URL=http://localhost:49234
NEXT_PUBLIC_ENVIRONMENT=local
EOF
```

---

## 4. Optional infrastructure (Docker)

Nothing is required: with `ENV=local` the backend runs on SQLite with no services.
Earlier versions of this page listed Kafka and Redis as requirements. No code talks to either (`kafka-streaming` in the register), so `docker-compose.yml` puts them behind a `future` profile.

```bash
docker compose up -d                        # PostgreSQL and Neo4j
docker compose --profile future up -d       # also Kafka, Kafka UI and Redis (nothing uses them)
docker compose down
```

| Service | URL / port | Notes |
|---|---|---|
| PostgreSQL | `localhost:5432` | User `causal`, password `causal_local_dev` (plain text in `docker-compose.yml`: a local-dev stack only). Used only when `ENV` is not `local` and `DATABASE_URL` points here, or by the tests through `TEST_DATABASE_URL` (§6). The schema is `backend/src/db/migrations/*.sql`, which the container applies only when its volume is first created: on an older volume, apply the newer files by hand (`psql -f`) or `docker compose down -v` to start over. |
| Neo4j Browser | `http://localhost:7474` | Bolt `localhost:7687`. Used when `NEO4J_URI` is set. |
| Kafka, Kafka UI, Redis | `9092`, `8080`, `6379` | `future` profile only. |

---

## 5. Start the application

### Option A: two launchers (the README's route)

```bash
bash launch_backend.sh                         # backend on http://127.0.0.1:49234
cd frontend && NEXT_PUBLIC_API_BASE_URL=http://localhost:49234 npm run dev
```

### Option B: `start.sh`

```bash
./start.sh            # backend (:49234) + frontend (:3000)
./start.sh backend    # backend only
./start.sh frontend   # frontend only
./start.sh test       # backend test suite
./start.sh e2e        # Playwright E2E tests (needs the running stack)
```

### Option C: by hand

```bash
cd backend
ENV=local DEV_AUTH_BYPASS=true .venv/bin/uvicorn src.api.main:app --reload --host 127.0.0.1 --port 49234
```

The frontend's built-in default API URL is `http://localhost:8000`; set `NEXT_PUBLIC_API_BASE_URL`
whenever the backend is on another port.

---

## 6. Verify

| Check | Command | Expected |
|---|---|---|
| Backend health | `curl http://localhost:49234/health` | `{"status":"ok", ...}` |
| API docs | open `http://localhost:49234/docs` | Swagger UI |
| Frontend | open `http://localhost:3000` | Next.js dev server |
| **The gate** | `bash .gnomon/ci.sh` | a per-step summary table, every step PASS or a named SKIPPED (`backend-pytest-postgres` is SKIPPED, "no TEST_DATABASE_URL", unless you give it a PostgreSQL server; see below) |

`.gnomon/ci.sh` runs every suite (backend, frontend, each package with its pre-registered gates),
the security steps and the claim checks, and is what CI runs. Its `pip-audit-*` steps audit the
virtual environments it runs in, pip included, so a venv built before a dependency floor was
raised fails them until it is upgraded (`.venv/bin/python -m pip install -U pip`, then the
install line above again). The audits ask a remote advisory database: offline, they are SKIPPED. Test counts are read from the tools each run, so none is
written here. Pieces, when iterating:

```bash
cd backend && .venv/bin/pytest -q                          # backend suite, on SQLite
cd packages/causal-core && .venv/bin/pytest                # kernel, incl. the T0 and T4-T8 gates
cd packages/causal-decision && ../causal-core/.venv/bin/pytest   # decision layer, incl. D0 and A0
cd frontend && npm run type-check && npx vitest run        # frontend
```

**The backend suite on PostgreSQL.** Set `TEST_DATABASE_URL` to a `postgresql+asyncpg://` URL and
the same suite runs on the schema the migrations build instead of SQLite: the test fixtures create
a fresh database on that server for the run (and drop it afterwards), so the URL's own database is
left alone, but its role must be allowed to create databases, roles and extensions. The compose
stack's server (§4) will do:

```bash
docker compose up -d postgres
export TEST_DATABASE_URL='postgresql+asyncpg://causal:causal_local_dev@localhost:5432/causalplatform'
cd backend && .venv/bin/pytest -q --no-cov                 # backend suite, on PostgreSQL
bash .gnomon/ci.sh                                         # the gate, backend-pytest-postgres included
```

`docs/TESTING_CICD.md` §3.1 has the details, and a disposable-container variant.

---

## 7. Try the MCP servers

**The kernel server (canonical).** `packages/causal-mcp`, console script `causal-mcp`:

```bash
packages/causal-core/.venv/bin/causal-mcp                   # stdio
packages/causal-core/.venv/bin/causal-mcp --http --port 8765   # stateless HTTP
```

Point an MCP client at it with `{"mcpServers": {"causal": {"command": "/abs/path/to/packages/causal-core/.venv/bin/causal-mcp"}}}`.
Its tools, flow and `data_path` rules: [`packages/causal-mcp/README.md`](../packages/causal-mcp/README.md).

**The legacy demo.** `backend/mcp_server/examples/client_demo.py` spawns the *legacy* server
(`python -m mcp_server.server`, console script `causal-platform-mcp`, deprecated) and calls two of
its tools; on its eight toy rows the estimate is a `LOW_N` refusal:

```bash
cd backend && .venv/bin/python -m mcp_server.examples.client_demo
```

---

## 8. Seed test data

```bash
cd backend
.venv/bin/python -m scripts.seed_data --devops       # service entities + graph fragments
.venv/bin/python -m scripts.seed_data --marketing    # customer churn model
.venv/bin/python -m scripts.seed_data --evaluation   # 30 days of evaluation metrics
.venv/bin/python -m scripts.seed_data --reviews      # objects for the governance queue
.venv/bin/python -m scripts.seed_data --incidents    # incidents with intervention verdicts
.venv/bin/python -m scripts.seed_data --all          # everything
.venv/bin/python -m scripts.seed_data --clear        # remove seeded data
```

With `ENV=local` and `DEV_AUTH_BYPASS=true`, the backend accepts the fixed dev token
`dev-local-token` (`backend/src/middleware/auth.py`) — it is not a real credential and is refused
in any other configuration:

```bash
curl -X POST http://localhost:49234/api/v1/interventions/feedback \
  -H "Authorization: Bearer dev-local-token" \
  -H "Content-Type: application/json" \
  -d '{"intervention_id": "<id>", "actual_effect": 0.45}'

curl http://localhost:49234/api/v1/interventions/accuracy \
  -H "Authorization: Bearer dev-local-token"
```

---

## 9. Project layout

```
causalplatform/
├── AGENTS.md                # entry point for coding agents
├── packages/
│   ├── causal-core/         # the causal kernel (identification, estimation, refutation)
│   ├── causal-decision/     # verdicts, decision ledger, outcome follow-up
│   ├── causal-mcp/          # the kernel's MCP server (canonical)
│   └── semantic-types/      # published TypeScript contract
├── backend/                 # FastAPI platform
│   ├── src/
│   │   ├── agents/          # platform agents
│   │   ├── api/             # REST routers
│   │   ├── db/              # engine factories (SQLite / PostgreSQL, Neo4j)
│   │   ├── skills/          # backend skills; causal ones delegate to the kernel
│   │   └── semantic_layer/  # Pydantic models + registry
│   ├── mcp_server/          # legacy MCP server (deprecated)
│   ├── tests/               # unit, integration, guardian
│   └── pyproject.toml
├── frontend/                # Next.js UI
├── benchmarks/              # pre-registered gates and their results
├── conformance/             # claim-contract schemas + the declared-not-implemented register
├── docs/                    # specifications, status page (CURRENT_STAGE.md), ADRs
├── prompts/                 # prompt specifications (not loaded at runtime)
├── scripts/                 # the gate's checks and ratchets
├── .gnomon/                 # agent harness surface; ci.sh is the gate
├── docker-compose.yml       # optional local services
├── launch_backend.sh        # backend launcher
└── start.sh                 # convenience launcher
```

---

## 10. Next steps

1. **Read the status page**, `docs/CURRENT_STAGE.md`: what is implemented, each row with its test.
2. **Run the gate**: `bash .gnomon/ci.sh`.
3. **Seed data** and explore the REST API at `http://localhost:49234/docs`.
4. **Call the kernel over MCP** (section 7).
5. **Contribute**: [`CONTRIBUTING.md`](../CONTRIBUTING.md).

---

## 11. Where to look

- **Working rules for agents:** `AGENTS.md`, `.gnomon/skills/`
- **What is implemented:** `docs/CURRENT_STAGE.md`
- **What is claimed but not implemented:** `conformance/declared_not_implemented.json`
- **Agent architecture (specification):** `docs/AGENTS.md`
- **Security boundaries:** `docs/CYBERSECURITY.md`
