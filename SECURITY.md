# Security Policy

## Supported versions

The platform is pre-1.0. Only the `main` branch receives security fixes. There
are no LTS branches yet.

| Version | Supported |
|---|---|
| `main` (HEAD) | ✅ |
| Tagged releases | best-effort |

## Reporting a vulnerability

**Please do not open public GitHub issues for vulnerabilities.** Report them
privately so they can be triaged and fixed before disclosure.

To report, use **GitHub Security Advisories**:

- Go to the repository's *Security* tab → *Advisories* → *New draft security advisory*
- This creates a private channel between you and the maintainer

This is the only supported reporting path. Please do not DM the maintainer on
any other platform for security issues.

Please include:

1. A description of the issue and the affected component (backend skill, MCP
   server, frontend, Guardian policy, etc.)
2. Steps to reproduce, with a minimal working example if possible
3. The impact you observed or assess (e.g. authentication bypass, audit-trail
   forgery, causal-graph poisoning, secrets exposure, DoS)
4. Any mitigations or workarounds you have identified
5. Your name / handle for the security advisory credits (or "anonymous")

## Response targets

These are best-effort targets, not contractual commitments:

| Severity | Initial acknowledgement | Fix or mitigation |
|---|---|---|
| Critical (RCE, auth bypass, data exfiltration) | within 48 hours | within 14 days |
| High (privilege escalation, audit-trail tampering) | within 72 hours | within 30 days |
| Medium (information disclosure, integrity issues) | within 7 days | within 60 days |
| Low (defense-in-depth, hardening) | within 14 days | next release window |

## Scope

In scope:
- The backend service (`backend/src/`) and its REST + MCP surfaces
- The Guardian policy engine and audit trail integrity
- The legacy MCP server (`backend/mcp_server/`, deprecated but still shipped) and its tool wrappers
- The frontend (`frontend/`) and any code that handles auth tokens
- The published packages under `packages/` (`causal-core`, `causal-decision`, `causal-mcp`)
- Build / packaging configuration (`pyproject.toml`, `package.json`, the `Dockerfile`s)
- The Docker Compose dev stack (`docker-compose.yml`). The repository contains no production
  deployment

Out of scope (please do not report):
- Issues that only affect the local-dev auth bypass (`DEV_AUTH_BYPASS=true`)
  — that path is intentionally permissive and never enabled in any non-`local`
  environment
- Denial-of-service through resource exhaustion against `localhost` deployments
- Third-party library CVEs without a demonstrated exploitation path in this
  codebase — please report those upstream first. The gate audits the
  hash-pinned Python locks CI installs from (`requirements/*.txt`, pip-audit)
  and `package-lock.json` (npm audit) on every push and weekly, and fails on
  any advisory not excused in `scripts/audit-allowlist.json`; CI also scans
  both container images with Trivy, and Dependabot opens update PRs. The
  published packages declare ranges, not pins, so a report that a version
  those ranges admit is affected is welcome
- Missing security headers on pages that do not handle authenticated state
- Issues in the research notes under `docs/research/`, which are background
  reading rather than part of the deployable product surface

## Coordinated disclosure

We follow standard coordinated disclosure: once a fix is available, we will:

1. Notify the reporter
2. Cut a patched release
3. Publish a security advisory with CVE assignment if applicable
4. Credit the reporter (with their consent)

Public disclosure should not occur before a patched release is available,
unless 90 days have elapsed without a fix.

## What is enforced today

Read against the code 2026-09-25. The target security design (database-layer
access control, secrets management, privacy controls, container scanning) is in
[`docs/CYBERSECURITY.md`](docs/CYBERSECURITY.md), which marks what is not built.
The gate's security steps (bandit, the secret scan, pip-audit and npm audit) are
in its §8.4.

**Authentication.** `FirebaseAuthMiddleware` (`backend/src/middleware/auth.py`)
requires a Firebase ID token (`verify_id_token`, revocation checked) on every
route except `/health`, `/docs`, `/redoc`, `/openapi.json` and CORS preflight. A
missing or invalid token gets 401.

**Local-dev bypass.** When `ENV=local` *and* `DEV_AUTH_BYPASS=true`, the fixed
token `dev-local-token` is accepted as a mock user holding `governance_officer`,
`platform_engineer`, `analyst` and `sre` -- between them every row of the role
matrix below -- and no `domains` claim, so every domain. Any other
combination falls through to Firebase verification.

**Roles.** Roles are read from the token's `roles` (list) or `role` (string)
custom claim. One matrix, `ROUTE_ROLES` in `backend/src/middleware/rbac.py`,
maps every state-changing route (and the audit-log, review-queue, provenance and
semantic-layer reads) to the roles that may call it, and each route depends on
its row; `backend/tests/integration/test_rbac_matrix.py` fails on a mounted
state-changing route without one. The route-by-route table, and the choices made
where the role design is silent, are in `docs/CYBERSECURITY.md` §4.0. In short:

- `GOVERNANCE_ROLES` = `governance_officer`, `platform_engineer`: approve, reject,
  the review queue, the audit log, model reviews (where a reviewer is also
  refused on edges they introduced), provenance reads and provenance erasure.
- `DECISION_ROLES` = `governance_officer`, `platform_engineer`, `sre`: deciding
  an intervention and recording its outcome.
- Drafting semantic objects and proposing causal models needs `analyst`,
  `graph_author`, `platform_engineer` or `agent`; recording a decision needs
  `analyst`, `sre` or a governance role.

A token with none of the platform roles is refused on every state-changing
route. Reads outside the semantic layer, the audit log and provenance need only
a valid token.

**Domain isolation.** The token's `domains` claim (a list) bounds which semantic
objects a user lists, reads, drafts into, and reviews; an object outside it is
not found. Without the claim, `governance_officer` and `platform_engineer` see
every domain and every other role sees none. This is enforced in the
application and, on PostgreSQL, again by the database: row-level security on
`causal_entities`, `causal_edges` and `graph_fragments` reads the caller's
scope, which the backend sets on every transaction, and binds `causal_app`, the
role the backend connects as. The backend refuses to start on PostgreSQL as a
superuser, a `BYPASSRLS` role or the tables' owner. Details in
`docs/CYBERSECURITY.md` §4.2.

**Guardian verdicts.** `GovernanceAgent` (`backend/src/agents/governance.py`)
refuses to start if `GUARDIAN_BYPASS_MODE` is set. The verdicts it issues are
HMAC-signed with `GUARDIAN_SIGNING_KEY`. Outside local development (`ENV` set to
anything but `local`) the backend refuses to start without that key, or with one
shorter than 32 bytes; locally, a per-process key is generated and the event is
logged at CRITICAL. A verdict also verifies against the retired keys listed in
`GUARDIAN_SIGNING_KEYS_PREVIOUS`, so rotating the key does not void verdicts
issued before it. Each verdict names the object it was issued for and the
action it rules on, both inside the signed claim, and can be spent once
(`verdict_consumption`). Approving a draft needs an APPROVED verdict issued for
`GOVERNANCE_APPROVE`.

**Secrets.** Every secret (the signing keys, `DATABASE_URL`, `NEO4J_PASSWORD`,
the LLM API keys) is read from the environment variable or from the file named
by `<NAME>_FILE`, the form Docker/Kubernetes secrets and secret-manager file
mounts use; never both. There is no built-in secret-manager client. The plain
passwords in `docker-compose.yml` (the owner's and `causal_app`'s) are the
local-dev exception. Details in
`docs/CYBERSECURITY.md` §8.2.

## Security model snapshot

This platform makes governance and audit a first-class concern. Some things to
keep in mind when evaluating issues:

- **Every write to the semantic-layer tables goes through a Guardian verdict.**
  In the backend (`backend/src`), `causal_entities`, `causal_edges`,
  `graph_fragments` and `assumptions` are written only by `SemanticLayerRegistry`
  (`backend/src/semantic_layer/registry.py`), which refuses a verdict that is
  missing, not APPROVED (REJECTED for a rejection), unsigned, issued for another
  object (or, for an approval, another action than `GOVERNANCE_APPROVE`), or
  already spent. The dev seeding script `backend/scripts/seed_data.py`
  inserts directly; it is run by hand against a local database, and no request
  path reaches it. A path that
  writes those tables without that check, or that obtains a verdict it should
  not, is critical-severity by default. The claim covers those tables only:
  other tables, such as `audit_log` itself or `interventions`, are written by
  their own endpoints.
- **Audit log is append-only**. Any path that allows deletion or rewriting of
  `audit_log` entries is critical-severity.
- **`EpistemicState`** is the single in-flight contract. Code that mutates it
  outside an agent boundary, or smuggles data past it, is in scope.
- **MCP wrappers must not raise**. They return a structured `{ok: False, ...}`
  envelope. A wrapper that crashes the server is in scope.
- **`DEV_AUTH_BYPASS=true`** is intentionally permissive in local dev. A code
  path that reaches that branch when `ENV != "local"` is in scope.
