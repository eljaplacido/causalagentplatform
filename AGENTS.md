# AGENTS.md — working in this repository

> **This file is instructions for coding agents** (Claude Code, Codex, Copilot, gnomon, ...)
> working *on* this repository. It is not [`docs/AGENTS.md`](docs/AGENTS.md), which is the
> *specification* of the agents the platform itself runs, and which describes more than the code
> does. When the two seem to disagree about how to work here, this file wins; when you need to
> know what the platform's agents are meant to do, read that one.

This is a causal-inference platform. Its product is numbers that people act on. The rules below
exist because an earlier version of this repository shipped heuristics, a constant confidence
interval and docs marking a phase finished over them.

## The canonical stack

| Where | What |
|---|---|
| `packages/causal-core` | The kernel: identification, estimation, refutation, sensitivity, segments. Typed contract, published schemas in `conformance/causal-claims/`. |
| `packages/causal-decision` | Evidence → verdict (`act`, `act_with_approval`, `experiment`, `refuse`), a hash-chained decision ledger, outcome follow-up and calibration. |
| `packages/causal-mcp` | The MCP server for the kernel: the repository's one MCP server. |
| `backend/` | FastAPI platform (governance, semantic layer, API). Its causal skills delegate to the kernel. It serves no MCP tools (its legacy MCP server was deleted in septa-6). |
| `frontend/` | Next.js UI. |
| `benchmarks/` | Pre-registered correctness gates (T0, T2–T9, D0, A0, C0) and every run's result. |
| `conformance/` | The claim contract's schemas and the register of known-false claims. |
| `.gnomon/` | The agent harness surface: roles, tools, skills, and the gate. |

## Rules

1. **Kernel first.** A new causal capability goes into `packages/causal-core` (or
   `packages/causal-decision`) with a typed contract, influence-function standard errors, a
   pre-registered benchmark and a seeded-defect negative control. Do not grow
   `backend/src/skills/`, and do not add an MCP server to `backend/`: agent-facing tools go in
   `packages/causal-mcp`.
2. **Causal numbers come from tools.** Every effect, interval, identification verdict or decision
   you state comes from a tool call, test run or script in this session. With causal-mcp the flow
   is `causal_check_graph` → `causal_identify` → `causal_analyze` (or `causal_decide` when the
   question is whether to act) → `causal_verify_narrative`, and `causal_impact` when the graph behind
   an earlier answer is revised. A refusal is a result: relay its
   reason and `missing_information`. The verdict is the tool's, not yours.
3. **Pre-register before you measure.** `benchmarks/<gate>/PRE-REGISTRATION.md`, with its TOML
   bars block, is committed before the run it judges. Failing runs are committed, never deleted.
   A bar changes only with a version bump and a changelog entry.
4. **Status claims are checked.** `conformance/declared_not_implemented.json` lists what the docs
   claim and the code does not do; `docs/CURRENT_STAGE.md` shows each row's status with its
   evidence. Citations are `file:line` plus a verbatim quote, checked by
   `scripts/status_claims.py`. If you move a cited line, re-cite it. An entry leaves only by being
   implemented (move it to `left_the_list` with `fixed_in` and `pinned_by`) or retracted, never by
   being dropped.
5. **Done is `.gnomon/ci.sh`** (or `septacore check`, which runs it). It runs the backend suite
   (on SQLite, then on PostgreSQL) and coverage floor, lint and type ratchets, the frontend, every
   package suite (with the T0, T4–T9, D0, A0 and C0 gates, and T2/T3 when their data is cached), the
   security scans (bandit, secrets, dependency advisories), gate scope, status claims, contract
   parity, doc figures, skill references (`scripts/skill_refs.py`: these instructions still point
   at real paths, scripts and steps) and doc reconciliation, and prints a per-step table. Paste the
   table. Exit 0 passed, 1 failed, 2 not measured (a SKIPPED step checked nothing), 3 docs owed a
   re-read. Only 0 is done.
6. **Work comes from the ledger.** `septacore ready` lists open work from `.beads/issues.jsonl`,
   each bead citing its provenance (usually a register id). Claim before starting; close only at
   exit 0.
7. **Tests assert known truth.** A test of a causal number compares against data simulated from a
   structural model whose effect you set, never against what the code returned last time.

## Where the details are

The skills in [`.gnomon/skills/`](.gnomon/skills/) are the working instructions; they are short
and each one is loaded by gnomon when a task matches it, but any agent can read them:

- `using-causal-mcp.md` — the MCP flow, `data_path` and `CAUSAL_MCP_DATA_ROOTS`, refusals
- `adding-an-estimator.md` — where a new method goes and what it ships with
- `benchmark-discipline.md` — pre-registration, result files, seeded defects
- `register-hygiene.md` — adding, re-citing and closing register entries
- `verifying-changes.md` — what ci.sh runs, its exit codes, the ratchets, the ledger
- `writing-tests.md`, `secrets.md`, `endpoints-and-models.md`

Setup and contribution workflow: [`CONTRIBUTING.md`](CONTRIBUTING.md) and
[`docs/QUICKSTART.md`](docs/QUICKSTART.md).
