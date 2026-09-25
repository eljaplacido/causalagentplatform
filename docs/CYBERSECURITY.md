# Cybersecurity, Governance & Compliance

> **Scope:** Security architecture, threat model, access control, data privacy, AI governance, and compliance posture for the Unified Enterprise Causal Platform.
>
> **This is the target security design, not a description of the running code.** The threat
> model (§2) and incident classes (§9) are the reference for triage. Most controls in the other
> sections are not built yet, and each section says so. What is enforced today (authentication,
> the two role checks, verdict binding, audit immutability) is in the root
> [`SECURITY.md`](../SECURITY.md#what-is-enforced-today). Controls this document once described
> as in place, and which are not, are listed in
> [`conformance/declared_not_implemented.json`](../conformance/declared_not_implemented.json)
> under the ids named below. Read against the code 2026-09-25.

---

## 1. Security Philosophy

The platform operates at the intersection of AI inference, causal decision-making, and enterprise operational data. The threat surface is not just technical—it includes **causal poisoning attacks**, **model manipulation**, and **governance circumvention**. Security is therefore layered across the data, model, governance, and infrastructure planes.

### Core Security Principles

These are design principles. Several are not yet met, and the sections below say which.

| Principle | Implication |
|---|---|
| **Zero trust** | No component trusts another by default. All calls are authenticated and authorized. |
| **Defense in depth** | No single control is the sole safeguard. Controls are layered. |
| **Guardian-enforced safety** | The GovernanceAgent is the final gate before any action affects live systems. It cannot be bypassed. |
| **Immutable audit** | All platform actions are append-only logged. No log entry is ever modified or deleted. |
| **Causal integrity** | Graph assets and assumptions are protected against unauthorized modification. Poisoning attacks must be detectable. |
| **Privacy by design** | PII is minimized at ingestion, pseudonymized in causal models, and never exposed in simulation outputs. |
| **Least privilege** | Agents, users, and services have only the permissions required for their specific function. |

---

## 2. Threat Model

### 2.1 Threat Actors

| Actor | Motivation | Risk Level |
|---|---|---|
| Malicious insider (privileged user) | Data exfiltration, model manipulation | High |
| Compromised service account | Lateral movement, unauthorized graph writes | High |
| Adversarial external user | Causal poisoning via crafted inputs | Medium |
| LLM prompt injection | Exfiltrate context, bypass Guardian | Medium |
| Supply chain attacker | Compromise transitive dependency in the Python stack | Medium |
| Compliance violator (accidental) | Unintentional PII in causal models | Medium |

### 2.2 Key Attack Vectors

Each vector lists its mitigations, and each mitigation is marked **in place**, **partial** or
**target**.

#### Causal Poisoning Attack
An adversary injects false or misleading causal edges or evidence into the graph to bias downstream recommendations or incident attributions.

**Mitigations:**
- **In place:** writing a graph object needs a signed, single-use Guardian `APPROVED` verdict
  issued for that object, and promotion to `APPROVED` needs a governance role.
- **Partial:** CausalEdge objects carry a version and a lineage list, but approval mutates
  the row in place and the version never changes (registers
  `semantic-objects-mutated-in-place`, `lineage-partial`).
- **In place, for causal models:** the assumption registry (`/api/v1/models`, migration
  `007_causal_models.sql`) keeps each model as immutable, content-addressed versions, append-only
  in the PostgreSQL schema. A revision may propose edges but not approve them; approving is a
  review that needs a governance role, and nobody may review an edge they introduced.
- **Partial:** the Guardian rejects a payload whose proposed edges form a cycle (PR-001). No
  other contradiction check triggers a review.
- **Target:** sensitivity analysis on every newly approved edge. PR-007 is declared and never
  evaluated (register `sensitivity-gate-not-enforced`).

#### Prompt Injection (LLM Plane)
Crafted user input or external data attempts to override agent instructions or exfiltrate EpistemicState contents.

No LLM is called anywhere in the platform today (register `llm-routing-no-callers`), so this
plane has no live surface yet. When one exists:
- Structured I/O contracts: agents accept typed inputs, never raw strings from untrusted
  sources. **In place** for the API, which validates request bodies with Pydantic.
- **Target:** hallucination-risk scoring of every LLM output. Nothing computes
  `hallucination_risk_score`. It defaults to 0.0, and `POST /api/governance/check` takes it from
  the request body, so the rule that reads it (PR-002) checks the caller's own number (register
  `guardian-risk-score-caller-supplied`).
- **Target:** input sanitization before any LLM skill call.
- **Target:** agent outputs validated against an output schema before any downstream action.

#### Guardian Circumvention
An agent or user attempts to write to the semantic layer without a Guardian verdict.

**Mitigations:**
- **In place:** the registry's write methods take a verdict id and refuse it unless it exists,
  is APPROVED (REJECTED for `reject_draft`), is signed by the Guardian, names this object, and
  is unspent (`backend/src/semantic_layer/registry.py`, `verdict.py`;
  `backend/tests/guardian/test_verdict_binding.py`).
- **In place, and narrower than "row security":** the database does not evaluate verdicts.
  `causal_edges.guardian_verdict_ref` and `interventions.guardian_verdict_ref` are
  `NOT NULL REFERENCES audit_log(id)`, so a row must point at *some* audit row. The
  `verdict_consumption` primary key makes each verdict spendable once. Whether the verdict is
  APPROVED and signed is checked only in application code. `causal_entities` and
  `graph_fragments` allow a NULL reference.
- **In place:** `GovernanceAgent` refuses to start when `GUARDIAN_BYPASS_MODE` is set.

#### Privilege Escalation
A lower-privilege user or agent attempts to access or modify resources above their permission tier.

**Mitigations:**
- **Partial:** RBAC at the API layer covers four governance routes, two intervention-decision
  routes and the model-review route (§4). **Target:** RBAC at the database and semantic layers.
- **Target:** agent identities as service accounts with scoped permissions. Agents run
  in-process today and have no identity of their own.
- **Target:** re-authentication on human escalation paths.

---

## 3. Authentication and Identity

### 3.1 User Authentication

**Current:** Firebase Auth ID tokens, verified by `FirebaseAuthMiddleware`
(`backend/src/middleware/auth.py`).
**Enterprise roadmap:** SAML 2.0 / OIDC federation for enterprise IdP (Okta, Entra ID, PingFederate)

What the middleware does:

```python
# FirebaseAuthMiddleware.dispatch, simplified
if request.method == "OPTIONS" or path in PUBLIC_PATHS:        # /health, /docs, /redoc, /openapi.json
    return await call_next(request)
token = extract_bearer_token(request)
if ENV == "local" and DEV_AUTH_BYPASS == "true" and token == "dev-local-token":
    request.state.user = DEV_MOCK_USER                        # all four roles
    return await call_next(request)
if not token:
    return JSONResponse(401, "Authentication required: Bearer token missing.")
try:
    request.state.user = firebase_auth.verify_id_token(token, check_revoked=True)
except Exception:
    return JSONResponse(401, "Authentication failed")
return await call_next(request)
```

The local-dev bypass needs all three conditions. The mock user carries the roles
`governance_officer`, `platform_engineer`, `analyst` and `sre`, so under the bypass every role
check passes.

### 3.2 Service-to-Service Authentication

> **Status:** target design. None of this is built.

All internal service calls use **short-lived tokens** issued by the identity service, not long-lived API keys.

- Cloud Run services: Google Cloud IAM service accounts (ADC pattern). The Firebase SDK and the
  Cloud SQL connector can use ADC; the repository contains no Cloud Run deployment.
- Agent-to-agent calls: signed JWTs with 15-minute expiry. The agents are in-process function
  calls today.
- Connector credentials rotated via Secret Manager (GCP). Today every credential, including
  the Guardian signing key, is read from environment variables (register `secrets-in-env`).

### 3.3 MFA Requirements

> **Status:** target design. The platform enforces no MFA. Whatever MFA exists is whatever the
> Firebase project is configured to require, and nothing checks for it.

| Role | MFA Required | Rationale |
|---|---|---|
| Governance Officer | Always required | High-stakes approval authority |
| Incident Commander | Always required | Operational action authority |
| Platform Engineer | Always required | Infrastructure access |
| Data Scientist (graph author) | Always required | Semantic layer write access |
| Business Analyst (read-only) | On sensitive operations | Limited blast radius |

---

## 4. Authorization: Role-Based Access Control (RBAC)

### 4.0 What is enforced

Roles come from the Firebase token's custom claims (`roles`, a list, or `role`, a string).
Exactly two role sets are checked (`require_role` in `backend/src/middleware/auth.py`):

| Role set | Members | Routes |
|---|---|---|
| `GOVERNANCE_ROLES` (`api/governance.py`) | `governance_officer`, `platform_engineer` | `POST /api/governance/approve/{id}`, `POST /api/governance/reject/{id}`, `GET /api/governance/review-queue`, `GET /api/governance/audit-log`, `POST /api/v1/models/{name}/reviews` (`api/models.py`, which also refuses a reviewer on their own edges) |
| `DECISION_ROLES` (`api/interventions.py`) | `governance_officer`, `platform_engineer`, `sre` | `POST /api/v1/interventions/{id}/approve`, `POST /api/v1/interventions/{id}/reject` |

Every other route needs only a valid token. That includes state-changing ones:
`POST /api/semantic/drafts`, `POST /api/governance/check`, `POST /api/v1/decisions` and its
`/followup`, `POST /api/v1/models` and `/{name}/versions`, `POST /api/v1/interventions/feedback`,
`POST /api/v1/session/run` and `/incident`, and `DELETE /api/v1/provenance/session/{id}`
(register `rbac-matrix-and-domain-isolation`).

### 4.1 Platform Roles

> **Status:** target design. The roles `viewer`, `analyst`, `graph_author` and `agent` are
> checked nowhere.

| Role | Semantic Layer | Simulation | Graph Write | Governance | Connectors | Audit Log |
|---|---|---|---|---|---|---|
| `viewer` | Read approved | Run | — | — | — | — |
| `analyst` | Read all | Run, save | Propose draft | — | — | — |
| `graph_author` | Read all | Run | Propose + submit | — | — | Read own |
| `governance_officer` | Read all | Run | Approve/reject | Full | — | Read all |
| `platform_engineer` | Read all | Run | Admin | Admin | Manage | Read all |
| `agent` (service) | Scoped read | Scoped run | Draft only | Read-only check | Scoped | Append-only |

### 4.2 Attribute-Based Overrides

> **Status:** target design, not in effect (register `rbac-matrix-and-domain-isolation`).

Domain isolation: a `domain` attribute on every semantic object restricts cross-domain reads to users with explicit cross-domain grants. A DevOps analyst cannot read supply chain causal graphs without an explicit grant from the Governance Officer.

Migration `001_initial.sql` declares row-level-security policies on `causal_entities` and
`causal_edges` that read `current_setting('app.user_domains')` and `app.user_role`. No
application code sets either. RLS does not apply to the table owner, which is the role
`docker-compose.yml` connects as, so the policies isolate nothing. Connecting as `causal_app`
would instead hide every row.

---

## 5. Data Privacy

> **Status:** target design. No pseudonymization, erasure or export control exists (register
> `gdpr-controls`). No connector runs in the platform today (DATA_LAYER.md §4), so nothing
> ingests operational data yet.

### 5.1 PII Handling in Causal Models

Causal inference over customer or employee data carries PII risk.

**Controls:**
- PII fields are **pseudonymized at ingestion** (tokenization via Secret Manager-stored keys)
- Causal models operate on pseudonymized identifiers, not real names or contact info
- Re-identification keys are never loaded into the causal runtime or agent context
- Simulation outputs report **population-level effects**, never individual-level counterfactuals with identifying information

### 5.2 Data Minimization

The connector `mapToSemanticObjects` step is the **only place** where raw operational data touches the causal layer. Connectors must:
- Strip fields not needed for causal analysis
- Pseudonymize before emitting to Kafka
- Log which fields were stripped (for lineage audit)

### 5.3 Data Residency

- EU customer data: Cloud SQL region locked to `europe-west1` (Belgium)
- US customer data: Cloud SQL region `us-central1`
- No cross-region replication of PII-containing tables without explicit tenant consent

---

## 6. AI Governance and Regulatory Compliance

### 6.1 EU AI Act Alignment

The platform falls under the EU AI Act as a **high-risk AI system** where it supports operational decisions in critical infrastructure (DevOps SRE) and HR/workforce contexts.

This table maps requirements to the control *intended* for each. It is not a conformity
assessment, and the Status column says how much of each control exists.

| EU AI Act Requirement | Platform Control | Status |
|---|---|---|
| Art. 9 – Risk management system | GovernanceAgent + Guardian policy engine | Partial: rules PR-001 to PR-006 and PR-008 evaluate; PR-007 does not; PR-002 and PR-009 read caller-supplied inputs |
| Art. 10 – Data governance | Semantic layer lineage, PII pseudonymization | Partial lineage (`lineage-partial`); no pseudonymization (`gdpr-controls`) |
| Art. 11 – Technical documentation | DATA_LAYER.md, AGENTS.md, assumption registries | Documents exist; the register lists where they overclaim |
| Art. 13 – Transparency | explanation_generate skill, assumption disclosure in outputs | Partial |
| Art. 14 – Human oversight | Human-in-the-loop approval on all graph changes and interventions | In place for graph promotion and intervention decisions (role-checked) |
| Art. 17 – Quality management | CausalOps validation harness, sensitivity tests | Pre-registered kernel benchmarks (TESTING_CICD.md); no sensitivity gate |

### 6.2 GDPR Alignment

> **Status:** target design (register `gdpr-controls`), except Art. 30, where the audit trail
> exists.

| GDPR Article | Platform Control |
|---|---|
| Art. 5 – Data minimization | Connector stripping at ingestion |
| Art. 17 – Right to erasure | Pseudonymization + key deletion (logical erasure) |
| Art. 20 – Data portability | Semantic layer export (JSON-LD format) |
| Art. 22 – Automated decision-making | Human approval gate before all interventions |
| Art. 30 – Records of processing | Audit trail with agent ID, action type, timestamp |

---

## 7. Causal Model Integrity Controls

### 7.1 Graph Integrity

> **Status:** partial. Only cycle detection runs, as Guardian rule PR-001 on the edges in a
> checked payload.

```
Proposed Edge → CausalDiscoveryAgent → Contradiction Check
                                              │
                              ┌───────────────┼───────────────┐
                         No conflict    Soft conflict     Hard conflict
                              │               │               │
                         Draft created   Flag + review    Reject + audit
                                        triggered          log entry
```

**Contradiction checks:**
- Cycle detection (DAG violation): **in place** (PR-001)
- Domain-constraint violation (known impossible edges per ontology): target
- Statistical inconsistency (edge contradicts p-value threshold from available data): target
- Confounder completeness check (treatment → outcome edge without known confounders flagged): target

### 7.2 Sensitivity Analysis Requirement

> **Status:** target design, not enforced (register `sensitivity-gate-not-enforced`). Rosenbaum
> bounds are not implemented (register `sensitivity-analysis`). E-values are computed by
> causal-core, when asked.

Every newly approved edge must pass a sensitivity analysis before it can be used in production simulations:
- Rosenbaum sensitivity bounds
- E-value computation for unmeasured confounders
- Results stored in `Assumption.sensitivityResult`

Edges that fail to meet minimum E-value thresholds are labeled `REQUIRES_REVIEW` and blocked from simulation use.

---

## 8. Infrastructure Security

### 8.1 Network Architecture

> **Status:** target design. The repository has Dockerfiles for the backend and frontend and a
> local `docker-compose.yml`. It has no cloud deployment.

```
Internet → Cloud Load Balancer (TLS 1.3 termination)
                → Cloud Armor (WAF, DDoS protection)
                    → Cloud Run (no public IPs on services)
                        → VPC Service Controls
                            → Cloud SQL (Private IP only)
```

### 8.2 Secrets Management

> **Status:** target design (register `secrets-in-env`). Today secrets are environment
> variables: `GUARDIAN_SIGNING_KEY` (without it, verdicts are signed with a per-process key, and
> the event is logged at CRITICAL), `DATABASE_URL`, `GOOGLE_APPLICATION_CREDENTIALS` (a key-file
> path), and the LLM provider API keys.

- All credentials in **Google Cloud Secret Manager**
- No secrets in environment variables, code, or config files
- Secret rotation enforced every 90 days for service accounts
- Secret access is logged and audited

### 8.3 Container Security

- **In place:** backend and frontend images run as non-root users (`USER causal`,
  `USER nextjs`). The backend image is `python:3.12-slim`.
- **Target:** read-only root filesystem where possible.
- **Target (register `security-scanning-not-in-ci`):** an SBOM for every release (Syft / Grype),
  and a Trivy scan as a CI gate that blocks on HIGH/CRITICAL.

### 8.4 Dependency Security

> **Status:** target design (register `security-scanning-not-in-ci`). `pip-audit` and `bandit`
> are declared as backend dev dependencies, and `bandit` has a config in
> `backend/pyproject.toml`. No CI step runs either. The only supply-chain gate in CI is the
> licence check on the published packages' runtime dependencies. Requirements are not
> hash-pinned.

**Python stack:**
- `pip-audit` in CI pipeline
- `safety` check on every dependency update
- Pinned dependencies with hash verification (`requirements.txt` with `--hash`)

---

## 9. Incident Response

### 9.1 Security Incident Classification

| Severity | Definition | Response Time |
|---|---|---|
| P0 | Guardian bypassed, audit trail tampered, causal poisoning confirmed | Immediate (< 15 min) |
| P1 | Unauthorized semantic layer write, PII exposure, privilege escalation | < 1 hour |
| P2 | Anomalous agent behavior, prompt injection attempt, dependency vulnerability | < 4 hours |
| P3 | Failed auth spike, drift in security metric, policy misconfiguration | < 24 hours |

The response times are targets for a deployed service. For this repository, the reporting
path and response targets are in [`SECURITY.md`](../SECURITY.md).

### 9.2 Detection Sources

> **Status:** target design. The only detection source that exists is the structured logs
> (`structlog`): for example `auth.token_invalid`, `auth.insufficient_role` and
> `guardian.signing_key.ephemeral`, and every verdict in `audit_log`. Nothing analyses them.

- Cloud Armor WAF alerts
- Audit trail anomaly detection (statistical deviation from baseline write patterns)
- Guardian verdict anomaly (unusual REJECTED rate → possible evasion attempt)
- Hallucination-risk score spike across agent pool (needs a score something computes; see §2.2)
- Secret Manager access anomaly (unexpected secret reads)

---

## 10. Security Testing Requirements

Checks every platform component must pass before promotion to any non-development environment.
The ticked ones exist as tests that `.gnomon/ci.sh` runs. The rest are not built (register
`security-scanning-not-in-ci`).

- [ ] SAST: Bandit (Python)
- [ ] Dependency audit: `pip-audit`
- [ ] Container scan: Trivy (block on HIGH/CRITICAL)
- [ ] DAST: OWASP ZAP against staging API (weekly)
- [x] Guardian bypass test: every registry write path enforces the verdict check
  (`backend/tests/guardian/test_guardian_bypass.py`, `test_verdict_binding.py`,
  `test_draft_cannot_self_approve.py`)
- [x] Audit trail integrity test: no audit entry can be updated or deleted, on the SQLite
  schema (`backend/tests/guardian/`). The PostgreSQL triggers are not exercised by any test
- [ ] Prompt injection test suite: structured adversarial input set for all LLM-facing endpoints
- [ ] Causal poisoning simulation: synthetic adversarial edge injection detected and rejected

---

## 11. Security Contact and Escalation

Report vulnerabilities through the private channel in [`SECURITY.md`](../SECURITY.md). Inside a
deployment, suspected causal poisoning or governance violations go to the Governance Officer
role; infrastructure incidents follow the P0/P1 path in §9. Do not attempt to resolve security
incidents autonomously via agent actions.
