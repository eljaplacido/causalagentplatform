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
- The MCP server (`backend/mcp_server/`) and its tool wrappers
- The frontend service worker and any code that handles auth tokens
- Build / packaging configuration (`pyproject.toml`, `package.json`, `Dockerfile`)
- Documented deployment paths (Docker Compose dev stack, Cloud Run prod stack)

Out of scope (please do not report):
- Issues that only affect the local-dev auth bypass (`DEV_AUTH_BYPASS=true`)
  — that path is intentionally permissive and never enabled in any non-`local`
  environment
- Denial-of-service through resource exhaustion against `localhost` deployments
- Third-party library CVEs without a demonstrated exploitation path in this
  codebase — please report those upstream first; we will pick them up via
  dependabot or equivalent
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

## Security model snapshot

This platform makes governance and audit a first-class concern. Some things to
keep in mind when evaluating issues:

- **Every semantic-layer write goes through Guardian** (`src/agents/governance.py`).
  Bypasses of `guardian_check` are critical-severity by default.
- **Audit log is append-only**. Any path that allows deletion or rewriting of
  `audit_log` entries is critical-severity.
- **`EpistemicState`** is the single in-flight contract. Code that mutates it
  outside an agent boundary, or smuggles data past it, is in scope.
- **MCP wrappers must not raise**. They return a structured `{ok: False, ...}`
  envelope. A wrapper that crashes the server is in scope.
- **`DEV_AUTH_BYPASS=true`** is intentionally permissive in local dev. A code
  path that reaches that branch when `ENV != "local"` is in scope.
