# Contributing to the Causal Platform

Thanks for considering a contribution. The platform makes causal claims that
operators act on, so the contribution bar leans toward "correct and audited"
rather than "fast and small." This document explains how to clear that bar.

## Before opening a PR

1. **Read [`docs/AGENTS.md`](docs/AGENTS.md)** — especially §4 (the
   `EpistemicState` contract) and §3 (the shared skill library). Almost every
   PR touches one of these surfaces.
2. **Read [`docs/CURRENT_STAGE.md`](docs/CURRENT_STAGE.md)** to see what
   phase we're in and which work streams are open. If your change doesn't fit
   the open streams, open an issue first to discuss.
3. **Read [`docs/TESTING_CICD.md`](docs/TESTING_CICD.md)** for the testing
   philosophy (causal correctness > functional correctness).

## How we work

### Branching

- `main` is the integration branch. CI must be green before merge.
- Branch from `main` using a descriptive prefix:
  - `feat/<short-slug>` — new feature or skill
  - `fix/<short-slug>` — bug fix
  - `refactor/<short-slug>` — non-behavioural change
  - `docs/<short-slug>` — documentation only
  - `test/<short-slug>` — test-only change
- Rebase, don't merge, when bringing `main` into your branch.

### Pull requests

A PR is review-ready when:

- [ ] **Title** follows `<type>: <imperative summary>` — e.g. `feat: expose
      sensitivity_analysis as an MCP tool`
- [ ] **Description** answers: what changed, why, how to verify
- [ ] **Tests** added or updated for any change in behaviour. Skill-level
      changes need both unit and integration tests. UI changes that affect
      causal reasoning surfaces need Playwright coverage.
- [ ] **Backend test suite passes** — `cd backend && .venv/bin/python -m pytest`
- [ ] **Frontend type-check passes** — `cd frontend && npx tsc --noEmit`
- [ ] **Frontend build passes** — `cd frontend && npx next build`
- [ ] **No regressions** in `docs/CURRENT_STAGE.md` exit criteria (don't
      mark a deliverable as done without a test that proves it)
- [ ] **Guardian integration** is exercised for any new write path
- [ ] **Audit-log entry** is written for any new agent action that mutates
      semantic state
- [ ] **DCO sign-off** on every commit (see below)

### Commits

We require [Developer Certificate of Origin](https://developercertificate.org/)
sign-off on every commit:

```bash
git commit -s -m "feat: add provenance_trace MCP tool"
```

This appends a `Signed-off-by:` trailer attesting that you have the right to
submit the change under the project's license (Apache 2.0).

Commit messages follow the conventional-commits style:

```
<type>: <short imperative summary>

<body — optional, wrap at 72 cols, explain the *why*>

Refs: #<issue>
Signed-off-by: Your Name <you@example.com>
```

`<type>` ∈ `feat | fix | refactor | docs | test | chore | perf | build | ci`.

## Setting up a dev environment

See [`docs/QUICKSTART.md`](docs/QUICKSTART.md) for the full guide. Short
version:

```bash
# Backend
cd backend
python -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest        # should be 551 passing

# Frontend
cd ../frontend
npm install
npx tsc --noEmit         # type-check
npx next build           # production build
```

## What kind of contributions we want

### High-value

- **New skills** in `src/skills/` — anything that extends causal reasoning
  capability and has unit + integration tests + a Guardian policy entry
- **MCP wrappers** for existing skills (especially `plan_action_sequence`,
  `provenance_trace`, `verify_data_contract`)
- **Connectors** in `src/connectors/` — Alertmanager, PagerDuty, GitHub
  webhooks, Datadog, Kubernetes (in roughly that order of priority)
- **Domain ontology templates** for Phase 4 (DevOps incident/change/alert)
- **Frontend type coverage** completing the last open Phase 1 item
- **DAG Workbench authoring UI** — propose-edge form, edge mutation, agent
  suggestion accept/reject
- **Documentation** clarifying how a specific skill or agent contract is
  meant to be used

### Lower-priority unless discussed first

- Major refactors of the orchestration graph or `EpistemicState` shape
- Replacing established dependencies (FastAPI, LangGraph, DoWhy, MCP SDK,
  Next.js)
- Performance optimisations without a benchmark showing a real bottleneck
- New persona pages — the existing personas are not yet feature-complete

### Out of scope

- Code that bypasses Guardian to "make tests faster"
- Adding mock data into production code paths (mock fallback must be
  env-gated; see `frontend/src/app/workbench/page.tsx` for the pattern)
- Vendor-locked integrations (e.g. hardcoding a specific LLM provider)

## Style

### Python

- `ruff` for linting (config in `backend/pyproject.toml`)
- Type hints on every public function; `mypy --strict` should be clean for
  any new module under `src/`
- Use `structlog.get_logger(__name__)` for new modules. Existing
  `logging.getLogger(__name__)` callers are being migrated; do not introduce
  `logger.info("event", arbitrary=kwarg)` syntax against a stdlib logger
  (it raises at runtime once handlers are attached — see
  `src/skills/effect_estimate.py:152` history)
- Docstrings: short summary, then args/returns. Skip on private helpers
  unless behaviour is non-obvious

### TypeScript

- Strict mode is non-negotiable
- Prefer named exports
- Components live in `frontend/src/components/<area>/`; pages live in
  `frontend/src/app/<route>/page.tsx`
- Canonical types come from `@causalplatform/semantic-types` —
  UI-specific extensions go in `frontend/src/lib/types.ts`. Don't redefine
  canonical types locally
- All network calls go through `frontend/src/lib/api.ts` clients so auth and
  request IDs are consistent

## Reporting bugs

Open a GitHub issue with:

1. What you expected to happen
2. What actually happened
3. Minimal reproduction (a curl command, a test case, or a screenshot)
4. Platform: Python version, Node version, OS

If the bug is a **security** issue, use the process in
[`SECURITY.md`](SECURITY.md) instead.

## Code of conduct

Participation is governed by [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md).
Be excellent to each other.

## License

By contributing, you agree that your contributions will be licensed under
the [Apache License, Version 2.0](LICENSE), as covered by the DCO sign-off
on each of your commits.
