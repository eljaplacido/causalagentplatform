# Cybersecurity, Governance & Compliance

> **Scope:** Security architecture, threat model, access control, data privacy, AI governance, and compliance posture for the Unified Enterprise Causal Platform.
>
> **This is the target security design, not a description of the running code.** The threat
> model (§2) and incident classes (§9) are the reference for triage. Most controls in the other
> sections are not built yet, and each section says so. What is enforced today (authentication,
> the role matrix and domain scope, verdict binding, audit immutability) is in the root
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
- **In place, narrower than "passes a sensitivity analysis":** the Guardian refuses to approve
  an edge that has no sensitivity result (PR-007): a `/check` for `GOVERNANCE_APPROVE` on an
  edge is REJECTED unless an assumption the edge references, not rejected in review, carries a
  stored `SensitivityResult`, read from the database rather than the request
  (`backend/tests/guardian/test_pr007_sensitivity_gate.py`). A result that is not robust still
  counts; the reviewer sees it.

#### Prompt Injection (LLM Plane)
Crafted user input or external data attempts to override agent instructions or exfiltrate EpistemicState contents.

No LLM is called anywhere in the platform today (register `llm-routing-no-callers`), so this
plane has no live surface yet. When one exists:
- Structured I/O contracts: agents accept typed inputs, never raw strings from untrusted
  sources. **In place** for the API, which validates request bodies with Pydantic.
- **In place:** grounding-risk scoring of prose submitted to `POST /api/governance/check`
  (`src/agents/grounding_risk.py`). Every number must be a claim of the kernel outcome the
  narrative reports, and causal wording needs a supported result; otherwise PR-002 rejects. The
  request cannot supply the score or the cycle count (422). When a session is named, that
  session supplies the domain and cycle count. This is a deterministic grounding check, not a
  model of hallucination, and it scores only the prose it is given.
- **Target:** input sanitization before any LLM skill call.
- **Target:** agent outputs validated against an output schema before any downstream action.

#### Guardian Circumvention
An agent or user attempts to write to the semantic layer without a Guardian verdict.

**Mitigations:**
- **In place:** the registry's write methods take a verdict id and refuse it unless it exists,
  is APPROVED, is signed by the Guardian, was issued for this action, names this object, and
  is unspent (`backend/src/semantic_layer/registry.py`, `verdict.py`;
  `backend/tests/guardian/test_verdict_binding.py`). The action is part of the signed claim,
  and each review decision accepts only a verdict issued for exactly that action:
  `promote_to_approved` one for `GOVERNANCE_APPROVE` (an APPROVED verdict from a check for any
  other action on the object used to approve it: `septa-60`,
  `backend/tests/guardian/test_approve_action_binding.py`), and `reject_draft` one for
  `GOVERNANCE_REJECT` (`backend/tests/integration/test_governance_reject_verdict.py`).
  Rejecting used to need a REJECTED verdict, which only an unclassified (DISORDER) check
  produced, so the authority to reject depended on the Guardian declining to classify
  (`septa-53`). A verdict for either review action authorises that decision and no other write.
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
- **In place:** RBAC at the API layer: one role matrix covers every state-changing route and
  the audit, review-queue, provenance and semantic-layer reads (§4.0), and domain scope bounds
  what a user sees of the semantic layer (§4.2). **In place:** the same domain scope at the
  database layer on PostgreSQL: row-level security on `causal_entities`, `causal_edges` and
  `graph_fragments`, fed per request from the token and binding the role the app connects as
  (§4.2). Role checks (which route a role may call) remain application-only.
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
`governance_officer`, `platform_engineer`, `analyst` and `sre`, and every row of the role
matrix grants at least one of them, so under the bypass every role check passes. It carries no
`domains` claim, so (holding `governance_officer`) it sees every domain.

### 3.2 Service-to-Service Authentication

> **Status:** target design. None of this is built.

All internal service calls use **short-lived tokens** issued by the identity service, not long-lived API keys.

- Cloud Run services: Google Cloud IAM service accounts (ADC pattern). The Firebase SDK and the
  Cloud SQL connector can use ADC; the repository contains no Cloud Run deployment.
- Agent-to-agent calls: signed JWTs with 15-minute expiry. The agents are in-process function
  calls today.
- Credentials, including the Guardian signing key, are read from an environment variable or
  a mounted secret file, with no secret-manager client (§8.2).

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
One matrix, `ROUTE_ROLES` in `backend/src/middleware/rbac.py`, maps each route below, by
method and full path, to the roles that may call it; holding one of them is enough. Every
state-changing route of the API is a row, and each route depends on its row through
`guard(method, path)`. `backend/tests/integration/test_rbac_matrix.py` walks the mounted app
and fails on a state-changing route without a row, then checks every row: a token holding no
granted role gets 403, a token holding a granted role does not.

| Route | Roles |
|---|---|
| `GET /api/semantic/entities[/{id}]`, `/fragments[/{id}]`, `/edges/{id}[/audit-trail]` | any platform role; a `viewer` reads APPROVED objects only; bounded by domain scope (§4.2) |
| `POST /api/semantic/drafts` | `analyst`, `graph_author`, `platform_engineer`, `agent` (`DRAFT_ROLES`); only into the user's domains |
| `POST /api/governance/check` | `graph_author`, `governance_officer`, `platform_engineer`, `agent` |
| `POST /api/governance/approve/{id}`, `/reject/{id}`, `GET /review-queue`, `GET /audit-log` | `governance_officer`, `platform_engineer` (`GOVERNANCE_ROLES`); approve, reject and the queue bounded by domain scope |
| `POST /api/v1/models/{name}/reviews` | `GOVERNANCE_ROLES`; a reviewer is also refused on edges they introduced |
| `POST /api/v1/models`, `POST /api/v1/models/{name}/versions` | `DRAFT_ROLES` |
| `POST /api/v1/decisions`, `POST /api/v1/decisions/{digest}/followup` | `analyst`, `governance_officer`, `platform_engineer`, `sre` |
| `POST /api/v1/interventions/{id}/approve`, `/reject`, `POST /api/v1/interventions/feedback` | `governance_officer`, `platform_engineer`, `sre` (`DECISION_ROLES`) |
| `POST /api/v1/session/run`, `/incident`, `POST /api/v1/decisions/verify-anchored`, `POST /api/v1/platform/evaluation/calibration` | any platform role |
| `GET /api/v1/provenance/trace/{id}`, `/export/{id}`, `/invocations/{id}`, `POST /api/v1/provenance/session/{id}/erasure` | `GOVERNANCE_ROLES` |

"Any platform role" is one of `viewer`, `analyst`, `graph_author`, `governance_officer`,
`platform_engineer`, `agent`, `sre`: a valid token with none of them is refused. Every other
read (decisions, models, incidents, interventions, platform metrics, session state) needs only
a valid token.

Where §4.1 does not say, these are the least-privilege choices made:

- **Decisions and follow-ups** write the decision ledger, so they need a role that may save a
  run (`analyst`, §4.1 "Run, save"), the operational decider (`sre`), or a governance role.
  `viewer`, `graph_author` and `agent` may not record decisions.
- **Model create and revise** propose edges, which is a draft (§4.1 "Graph Write"): the
  drafting roles. A `governance_officer` reviews models and does not author them; a person who
  should do both holds both roles.
- **Intervention feedback** records what an intervention did; whoever may decide one may
  record its outcome (`DECISION_ROLES`).
- **`/api/governance/check`** issues the verdicts that authorise writes. §4.1 gives `agent` a
  read-only check and `graph_author` "submit"; `analyst` drafts through `/drafts`, which runs
  its own check, and gets no direct `/check`.
- **Sessions** run the platform graph; §4.1 gives every role "Run".
- **Provenance** is read like the audit log (governance roles only), and so is its erasure.
  Provenance is append-only (`011_provenance.sql`: no role holds UPDATE or DELETE, and a trigger
  refuses both); an erasure writes a tombstone naming who, when and why, which hides the
  session's earlier records from the provenance reads and deletes nothing.
- **`graph_author` "Audit Log: Read own"** is not provided: the audit log stays governance-only.
- **`sre`** is not in §4.1. It is the incident-commander role that decides interventions, and
  it reads all of the semantic layer.

### 4.1 Platform Roles

> **Status:** in effect at the API layer, as the matrix in §4.0 implements it. The
> Connectors column has no routes (no connector runs), and `graph_author`'s "Read own" audit
> access is not provided.

| Role | Semantic Layer | Simulation | Graph Write | Governance | Connectors | Audit Log |
|---|---|---|---|---|---|---|
| `viewer` | Read approved | Run | — | — | — | — |
| `analyst` | Read all | Run, save | Propose draft | — | — | — |
| `graph_author` | Read all | Run | Propose + submit | — | — | Read own |
| `governance_officer` | Read all | Run | Approve/reject | Full | — | Read all |
| `platform_engineer` | Read all | Run | Admin | Admin | Manage | Read all |
| `agent` (service) | Scoped read | Scoped run | Draft only | Read-only check | Scoped | Append-only |

### 4.2 Attribute-Based Overrides

> **Status:** in effect at the application layer, and on PostgreSQL at the database layer too
> (row-level security, below). SQLite, the local-development engine, has only the first.

Domain isolation: a `domain` attribute on every semantic object restricts cross-domain reads to users with explicit cross-domain grants. A DevOps analyst cannot read supply chain causal graphs without an explicit grant from the Governance Officer.

What enforces it is the token's `domains` claim, a list of domain names
(`domain_scope` in `backend/src/middleware/rbac.py`), applied in the semantic API and in the
registry's queries:

- Entities, edges and fragments are listed, and read by id, only inside the user's domains.
  An object outside them is 404, not 403, so its existence is not disclosed. An edge's audit
  trail follows its edge.
- The review queue lists, and approve and reject act on, only objects inside the reviewer's
  domains.
- A draft into a domain outside the user's is refused (403) before the Guardian runs, so it
  writes no audit row.
- **No `domains` claim** means every domain for `governance_officer` and `platform_engineer`,
  and no domain at all for every other role: fail closed. A claim that is present binds every
  role, including those two. A malformed claim is the empty set.
- The grant itself is a custom claim set in the identity provider; the platform has no route
  that issues one.
- Causal models (`/api/v1/models`), decisions and sessions carry no domain and are not scoped.

**The database's line.** On PostgreSQL the same scope is enforced a second time, by
row-level security, so a query that forgets the application's filter still returns nothing
outside the caller's domains:

- **Policies.** Migration `010_rls_binds.sql` puts one policy on each of `causal_entities`,
  `causal_edges` and `graph_fragments`, `USING` and `WITH CHECK` the predicate
  `app_scope_allows(domain)`: the row's domain is in `app.user_domains`, or no domain list is
  set and `app.user_role` holds `governance_officer`, `platform_engineer` or the service
  marker. So the database applies the rule above itself -- a present claim binds every role --
  and a write into another domain is refused, not just hidden. (001's policies, which this
  replaces, split the list on commas and let the two governance roles through whatever their
  claim said; `graph_fragments` had none.)
- **Settings, per request and per transaction.** The auth middleware binds the caller's scope
  (`rbac.scope_for_user`) around the rest of the request, in a context variable;
  `backend/src/db/scope.py` opens every PostgreSQL transaction with
  `set_config('app.user_domains', …, true)` and `set_config('app.user_role', …, true)`.
  Transaction-local, so a pooled connection never carries one request's scope into the next.
  `app.user_domains` is a JSON array of domain names, or empty exactly when the user is
  unscoped (no claim, a governance role); `app.user_role` is a JSON array of the user's
  platform roles.
- **No user.** Outside a request the scope is empty: nothing visible, every write refused.
  Code that acts for no one -- the seeder, a background job, an agent run outside a request --
  must declare `service_scope(reason)`, which sees every domain; forgetting it fails closed.
  An agent run inside a request acts for that request's user and gets that user's scope. No
  token can claim the service marker: only the platform's own roles are forwarded.
- **The connection role.** RLS binds neither a superuser, a `BYPASSRLS` role nor the tables'
  owner, so the app connects as `causal_app`, which owns nothing (docs/QUICKSTART.md). The
  owner remains the migration and repair role; `FORCE ROW LEVEL SECURITY` is not set. The
  backend refuses to start on PostgreSQL as a role the policies would not bind.
- **Grants.** `causal_app` may select and insert on every table, update the ones that are not
  append-only, and use the sequences; it has no `DELETE` or `TRUNCATE` anywhere, and the
  append-only triggers stay. `backend/tests/integration/test_rls_domain_isolation.py` pins all
  of this on PostgreSQL, including negative controls with a policy dropped, RLS disabled, and
  the owner's connection.

What the database does not check: which route a role may call (the matrix in §4.0 is
application-only), and the tables that carry no domain.

---

## 5. Data Privacy

> **Status:** target design. No pseudonymization, erasure or export control exists (register
> `gdpr-controls`). The provenance erasure (§4.0) hides records from the API and keeps them in
> the database, so it is not Art. 17 erasure. No connector runs in the platform today
> (DATA_LAYER.md §4), so nothing ingests operational data yet.

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
- Pseudonymize before returning draft objects (connectors emit nothing to a broker; there is none)
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
| Art. 9 – Risk management system | GovernanceAgent + Guardian policy engine | Partial: rules PR-001 to PR-008 evaluate (PR-007 on edge approvals); PR-009 reads caller-supplied inputs; PR-002's score is computed from the prose checked |
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

> **Status:** partly enforced. Guardian PR-007 refuses a `GOVERNANCE_APPROVE` check for a
> stored edge that has no sensitivity result on an assumption it rests on (`septa-33`,
> `backend/tests/guardian/test_pr007_sensitivity_gate.py`). It asks for a result, not a robust
> one: a result that says not robust still counts, and the reviewer sees it in the queue's risk
> tier (`septa-71`). Rosenbaum bounds are not implemented (register `sensitivity-analysis`).
> E-values are computed by causal-core, when asked. Nothing labels an edge `REQUIRES_REVIEW` or
> blocks it from simulation use.

The target: every newly approved edge carries a sensitivity analysis before it is used in
production simulations:
- Rosenbaum sensitivity bounds
- E-value computation for unmeasured confounders
- Results stored in `Assumption.sensitivityResult`

and edges that fail to meet minimum E-value thresholds are labeled `REQUIRES_REVIEW` and blocked
from simulation use.

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

**Supported mechanism: an environment variable, or a file it names.** Every secret the backend
reads goes through `read_secret` in `backend/src/secret_source.py`. For a secret `NAME` it takes
exactly one of `NAME_FILE` (a path; the value is the file's contents less one trailing newline) or
`NAME` (the value itself). Both set, or a `NAME_FILE` that is missing, unreadable or empty, stops
the read with an error naming the variable and the path, never the value. The file form is how
Docker and Kubernetes secrets and the secret managers' file-mount (CSI) drivers deliver a
secret, and it keeps the value out of the process environment. There is **no built-in
secret-manager client**: a secret manager is supported by mounting its secret as a file (or
injecting it as a variable), and access logging and rotation schedules are the manager's.

| Secret | Read as | Used for |
|---|---|---|
| `GUARDIAN_SIGNING_KEY` | `_FILE` or env | The HMAC key every Guardian verdict is signed with |
| `GUARDIAN_SIGNING_KEYS_PREVIOUS` | `_FILE` or env; comma- or newline-separated | Retired keys that still verify, never sign |
| `DATABASE_URL` | `_FILE` or env | PostgreSQL, outside `ENV=local` without a Cloud SQL socket |
| `NEO4J_PASSWORD` | `_FILE` or env | Neo4j, when `NEO4J_URI` is set |
| `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `DEEPSEEK_API_KEY` | `_FILE` or env | LLM clients (nothing calls `get_llm` today) |
| `GOOGLE_APPLICATION_CREDENTIALS` | env, already a path to a key file | Firebase Admin; or ADC with no file at all |

**Start-up refusal.** Local development is `ENV` unset or `local` (case and whitespace
ignored), the same rule that chooses SQLite in `backend/src/db/factory.py`. With any other `ENV`
the application refuses to start (the FastAPI lifespan raises `SigningKeyError` before it serves
a request) when `GUARDIAN_SIGNING_KEY` is missing or shorter than 32 bytes, or a previous key is
shorter than 32 bytes. In local development a missing key is replaced by one generated for the
process, logged at CRITICAL (`guardian.signing_key.ephemeral`): its verdicts do not survive a
restart or reach another worker. Pinned by `backend/tests/guardian/test_signing_key_rotation.py`.

**Rotating the signing key.** New verdicts are signed with `GUARDIAN_SIGNING_KEY` only; a
signature verifies against it or any key in `GUARDIAN_SIGNING_KEYS_PREVIOUS`. To rotate: move the
current key into the previous list, set the new one, restart every worker. Verdicts issued
before the rotation and not yet spent still authorise their one write. Drop the old key from the
list once they have been spent (or to void them deliberately, if the old key leaked). Nothing
schedules or enforces a rotation.

**The local-dev exception.** `docker-compose.yml` carries the local PostgreSQL and Neo4j
password (`causal_local_dev`), and the login it gives `causal_app` (`causal_app_local_dev`), in
plain text. It is a development stack and these passwords protect nothing else; do not reuse
them, or the compose file, for a deployment.

**Not built:** a secret-manager client, rotation enforced on a schedule, and audit of secret
access inside the platform.

### 8.3 Container Security

- **In place:** backend and frontend images run as non-root users (`USER causal`,
  `USER nextjs`). The backend image is `python:3.12-slim`, the frontend image `node:24-alpine`,
  each pinned by digest (Dependabot moves the digest). The backend installs its Python packages
  from the hash-pinned lock `requirements/image.txt` (§8.4) and keeps no pip at run time; the
  frontend installs `package-lock.json` with `npm ci` and runs Next's standalone server with npm
  removed. Both build from the repository root; the frontend's Dockerfile did not build at all
  until septa-49 (it built from `frontend/`, which has no lock).
- **Image scan (CI job `images`, `.github/workflows/ci.yml`).** Every push, pull request and
  weekly run builds both images and scans them with Trivy 0.74.0 (the `aquasec/trivy` image,
  pinned by digest). A HIGH or CRITICAL vulnerability **with a published fix** fails the job
  unless `scripts/image-scan-allowlist.json` excuses it with a reason and a `review_by` date
  (`scripts/image_scan.py`; an expired or stale entry fails, and so does a scan that listed no
  package). The allowlist is empty. A finding **with no fixed version published** is listed and
  counted, not failed, since no upgrade can fix it. On 2026-09-26 the backend image had 44 such
  HIGH findings, all in Debian 13 base packages (util-linux, ncurses, systemd, acl, perl-base),
  and the frontend image none. The job also keeps a CycloneDX SBOM of each image as an artifact.
  Negative controls: `backend/tests/unit/test_image_scan.py`.
- **Target:** read-only root filesystem where possible.

### 8.4 Dependency Security

**In place** (`.gnomon/ci.sh`, on every push and weekly on a schedule, so an advisory
published since the last push surfaces without one):

- **Dependency audit.** `pip-audit` over the hash-pinned locks (`requirements/backend.txt`,
  `kernel.txt` and `image.txt`, pip included), which the `python-lock` step holds equal to the
  environments the gate tests, and `npm audit` over `package-lock.json` (steps
  `pip-audit-backend`, `pip-audit-packages`, `pip-audit-image`, `npm-audit`, through
  `scripts/dep_audit.py`).
  Any advisory fails the step unless `scripts/audit-allowlist.json` excuses it with a reason
  and a `review_by` date; an expired entry fails, and so does one whose advisory is no longer
  reported. The list is empty: every advisory found when the step was added was fixed by
  raising a floor in `backend/pyproject.toml` (transitive dependencies included) or
  upgrading in `package-lock.json`. The advisory databases are remote; when one cannot be
  reached the step is SKIPPED (not measured), which fails CI.
- **SAST.** `bandit` over `backend/src` and every `packages/*/src`,
  blocking on MEDIUM and HIGH findings, held at zero by the ratchet in
  `scripts/lint-baseline.json` (steps `bandit-backend`, `bandit-packages`). The backend's
  `[tool.bandit]` skips no rule. B608 (SQL built from strings) was skipped for the whole
  tree until its 13 sites in `backend/src` were reviewed one by one: none put a request
  value into SQL text (each interpolated a fixed fragment, a placeholder list or a table
  name from a closed mapping), and each was rewritten as a literal statement or as
  SQLAlchemy Core over a lightweight table, with no `# nosec`. Request values reach SQL
  only as bound parameters; `backend/tests/integration/test_sql_filters_bound.py` sends
  SQL-shaped filter, status and domain values through the audit-log, incident, decision
  and registry queries and checks they match nothing and change nothing. B608 is now
  enforced, so a new string-built statement fails the gate. B101 (assert) is not skipped
  either; it is LOW, so bandit reports it without counting it.
- **Secret scan.** `scripts/secret_scan.py` (step `secret-scan`) over every file git would
  commit from the working tree: private-key blocks, AWS, Google, Azure, GitHub, Slack, OpenAI,
  Anthropic and Stripe token formats, and high-entropy literals assigned to `*KEY`, `*SECRET`,
  `*TOKEN` and `*PASSWORD` names. It prints a fingerprint, never the value; a test fixture that
  must look like a key is excused in `scripts/secret-scan-allowlist.json`. It does not scan
  the git history: a key that was ever committed is handled by revoking it.
- The licence check on the published packages' runtime dependencies (a CI step before the gate).

- **Hash-pinned Python installs.** Every Python environment CI builds installs from a lock in
  `requirements/` (`backend.txt`, `kernel.txt`, `image.txt` for the backend image, `release.txt`
  for the published wheels), made by `uv pip compile --generate-hashes` and installed with
  `pip install --require-hashes --no-deps`: every distribution pinned, every file checked against
  its sha256. The repository's own packages then install from the checkout with `--no-deps
  --no-build-isolation`, so they fetch nothing, not even a build backend. The gate's
  `python-lock` step (`scripts/py_lock.py`) fails a lock that is not current with the
  `pyproject.toml` files it was made from, and a tested environment that differs from its lock;
  the `pip-audit-*` steps audit the locks. `package-lock.json` pins npm with integrity hashes.
- **Release SBOM.** `release.yml` writes a CycloneDX 1.6 SBOM of the published packages'
  runtime environment (the built wheels plus `requirements/release.txt`), checks that it lists
  exactly that (`scripts/sbom_check.py`), and attaches it to the tag's GitHub release.
- **Update PRs.** `.github/dependabot.yml`: weekly, grouped, for the Python locks and ranges,
  npm, the GitHub Actions and the base images. It ignores the pins the ratchets count per
  version (ruff, mypy, bandit, pip-audit), semver-majors in pyprojects and npm (ledger work), node lines that are not LTS, and
  the specific majors whose ledger items are open (TESTING_CICD.md §5.4).
- **Where CI runs.** Jobs run where the repository variable `CI_RUNS_ON` says: GitHub's hosted
  runners when it is unset, otherwise self-hosted runners (label `causalplatform-ci`). A
  self-hosted job executes the repository's code, including a dependency update's install
  scripts, on that host as the runner's user; the repository is private, so no outside party can
  open a pull request that runs there. The job's database listens on loopback only.
- The Python build tools the release uses (`build`, `twine`, `cyclonedx-bom`) are pinned by
  version, not by hash. `safety` is not run; `pip-audit` covers the same advisories.

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
- Grounding-risk rejections (PR-002) spiking across the agent pool (the score is computed per `/check`, see §2.2; nothing aggregates it yet)
- Secret Manager access anomaly (unexpected secret reads)

---

## 10. Security Testing Requirements

Checks every platform component must pass before promotion to any non-development environment.
The ticked ones exist as steps or tests that `.gnomon/ci.sh` or a CI job runs. The rest are not
built (DAST: register `dast-not-built`).

- [x] SAST: Bandit (Python), MEDIUM+ held at zero (`bandit-backend`, `bandit-packages`)
- [x] Dependency audit: `pip-audit` and `npm audit` against a reviewed allowlist
  (`pip-audit-backend`, `pip-audit-packages`, `npm-audit`)
- [x] Secret scan of the working tree (`secret-scan`; controls in
  `backend/tests/unit/test_secret_scan.py`)
- [x] Container scan: Trivy over both images, blocking on HIGH/CRITICAL with a published fix
  (CI job `images`; findings with no fix published are listed, not blocked)
- [ ] DAST: OWASP ZAP against a staging API (weekly). Not built: there is no staging
  environment, and nothing runs a dynamic scanner against a running API (register
  `dast-not-built`)
- [x] Guardian bypass test: every registry write path enforces the verdict check
  (`backend/tests/guardian/test_guardian_bypass.py`, `test_verdict_binding.py`,
  `test_draft_cannot_self_approve.py`)
- [x] Audit trail integrity test: no audit entry can be updated or deleted
  (`backend/tests/guardian/test_guardian_bypass.py`), on the SQLite schema and, in the gate's
  `backend-pytest-postgres` step, on the PostgreSQL schema the migrations build, where
  001's triggers are what refuse it (and 003/004/007's for the decision chain and models)
- [ ] Prompt injection test suite: structured adversarial input set for all LLM-facing endpoints
- [ ] Causal poisoning simulation: synthetic adversarial edge injection detected and rejected

---

## 11. Security Contact and Escalation

Report vulnerabilities through the private channel in [`SECURITY.md`](../SECURITY.md). Inside a
deployment, suspected causal poisoning or governance violations go to the Governance Officer
role; infrastructure incidents follow the P0/P1 path in §9. Do not attempt to resolve security
incidents autonomously via agent actions.
