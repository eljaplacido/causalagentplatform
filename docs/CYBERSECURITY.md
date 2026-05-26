# Cybersecurity, Governance & Compliance

> **Scope:** Security architecture, threat model, access control, data privacy, AI governance, and compliance posture for the Unified Enterprise Causal Platform. Every platform component must conform to the controls defined here.

---

## 1. Security Philosophy

The platform operates at the intersection of AI inference, causal decision-making, and enterprise operational data. The threat surface is not just technical—it includes **causal poisoning attacks**, **model manipulation**, and **governance circumvention**. Security is therefore layered across the data, model, governance, and infrastructure planes.

### Core Security Principles

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

#### Causal Poisoning Attack
An adversary injects false or misleading causal edges or evidence into the graph to bias downstream recommendations or incident attributions.

**Mitigations:**
- All graph writes require Guardian `APPROVED` verdict
- CausalEdge objects are versioned and lineage-traced
- Contradictory edges trigger automated GovernanceAgent review
- Sensitivity analysis run on all new approved edges

#### Prompt Injection (LLM Plane)
Crafted user input or external data attempts to override agent instructions or exfiltrate EpistemicState contents.

**Mitigations:**
- Structured I/O contracts: agents accept typed inputs, never raw strings from untrusted sources
- H-Neuron hallucination risk scoring on every LLM output
- Input sanitization before any LLM skill call
- Agent outputs are validated against output schema before any downstream action

#### Guardian Circumvention
An agent or user attempts to write to the semantic layer without a Guardian verdict.

**Mitigations:**
- Semantic layer write endpoints are middleware-protected: Guardian verdict ID is a required parameter
- Guardian verdict IDs are validated against the audit log before writes proceed
- Database-level row security prevents writes without valid `guardian_verdict_ref`

#### Privilege Escalation
A lower-privilege user or agent attempts to access or modify resources above their permission tier.

**Mitigations:**
- RBAC enforced at API layer, database layer, and semantic layer
- Agent identities are service accounts with scoped permissions
- Human escalation paths require re-authentication

---

## 3. Authentication and Identity

### 3.1 User Authentication

**Current:** Firebase Auth (JWT-based)
**Enterprise roadmap:** SAML 2.0 / OIDC federation for enterprise IdP (Okta, Entra ID, PingFederate)

```python
# Firebase JWT middleware pattern
# Lazy SDK initialization to avoid startup failures in restricted environments
@app.middleware("http")
async def firebase_jwt_middleware(request: Request, call_next):
    token = extract_bearer_token(request)
    if token:
        decoded = await verify_firebase_token(token)  # Lazy SDK init
        request.state.user = decoded
    return await call_next(request)
```

### 3.2 Service-to-Service Authentication

All internal service calls use **short-lived tokens** issued by the identity service, not long-lived API keys.

- Cloud Run services: Google Cloud IAM service accounts (ADC pattern)
- Agent-to-agent calls: signed JWTs with 15-minute expiry
- Connector credentials: rotated via Secret Manager (GCP), never in environment variables or code

### 3.3 MFA Requirements

| Role | MFA Required | Rationale |
|---|---|---|
| Governance Officer | Always required | High-stakes approval authority |
| Incident Commander | Always required | Operational action authority |
| Platform Engineer | Always required | Infrastructure access |
| Data Scientist (graph author) | Always required | Semantic layer write access |
| Business Analyst (read-only) | On sensitive operations | Limited blast radius |

---

## 4. Authorization: Role-Based Access Control (RBAC)

### 4.1 Platform Roles

| Role | Semantic Layer | Simulation | Graph Write | Governance | Connectors | Audit Log |
|---|---|---|---|---|---|---|
| `viewer` | Read approved | Run | — | — | — | — |
| `analyst` | Read all | Run, save | Propose draft | — | — | — |
| `graph_author` | Read all | Run | Propose + submit | — | — | Read own |
| `governance_officer` | Read all | Run | Approve/reject | Full | — | Read all |
| `platform_engineer` | Read all | Run | Admin | Admin | Manage | Read all |
| `agent` (service) | Scoped read | Scoped run | Draft only | Read-only check | Scoped | Append-only |

### 4.2 Attribute-Based Overrides

Domain isolation: a `domain` attribute on every semantic object restricts cross-domain reads to users with explicit cross-domain grants. A DevOps analyst cannot read supply chain causal graphs without an explicit grant from the Governance Officer.

---

## 5. Data Privacy

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

- EU customer data: Cloud SQL region locked to `europe-west1` (Frankfurt)
- US customer data: Cloud SQL region `us-central1`
- No cross-region replication of PII-containing tables without explicit tenant consent

---

## 6. AI Governance and Regulatory Compliance

### 6.1 EU AI Act Alignment

The platform falls under the EU AI Act as a **high-risk AI system** where it supports operational decisions in critical infrastructure (DevOps SRE) and HR/workforce contexts.

| EU AI Act Requirement | Platform Control |
|---|---|
| Art. 9 – Risk management system | GovernanceAgent + Guardian policy engine |
| Art. 10 – Data governance | Semantic layer lineage, PII pseudonymization |
| Art. 11 – Technical documentation | DATA_LAYER.md, AGENTS.md, assumption registries |
| Art. 13 – Transparency | explanation_generate skill, assumption disclosure in outputs |
| Art. 14 – Human oversight | Human-in-the-loop approval on all graph changes and interventions |
| Art. 17 – Quality management | CausalOps validation harness, sensitivity tests |

### 6.2 GDPR Alignment

| GDPR Article | Platform Control |
|---|---|
| Art. 5 – Data minimization | Connector stripping at ingestion |
| Art. 17 – Right to erasure | Pseudonymization + key deletion (logical erasure) |
| Art. 20 – Data portability | Semantic layer export (JSON-LD format) |
| Art. 22 – Automated decision-making | Human approval gate before all interventions |
| Art. 30 – Records of processing | Audit trail with agent ID, action type, timestamp |

### 6.3 MAP-PRICE-RESOLVE Compliance Framework

Established patterns in the platform:
- **MAP:** Entity extraction and compliance scoring
- **PRICE:** Cost allocation and pricing governance
- **RESOLVE:** Escalation resolution with human approval

18+ governance routes expose compliance scoring and escalation management to Governance Officers.

---

## 7. Causal Model Integrity Controls

### 7.1 Graph Integrity

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
- Cycle detection (DAG violation)
- Domain-constraint violation (known impossible edges per ontology)
- Statistical inconsistency (edge contradicts p-value threshold from available data)
- Confounder completeness check (treatment → outcome edge without known confounders flagged)

### 7.2 Sensitivity Analysis Requirement

Every newly approved edge must pass a sensitivity analysis before it can be used in production simulations:
- Rosenbaum sensitivity bounds
- E-value computation for unmeasured confounders
- Results stored in `Assumption.sensitivityResult`

Edges that fail to meet minimum E-value thresholds are labeled `REQUIRES_REVIEW` and blocked from simulation use.

---

## 8. Infrastructure Security

### 8.1 Network Architecture

```
Internet → Cloud Load Balancer (TLS 1.3 termination)
                → Cloud Armor (WAF, DDoS protection)
                    → Cloud Run (no public IPs on services)
                        → VPC Service Controls
                            → Cloud SQL (Private IP only)
                            → Neo4j (Private IP only)
                            → Kafka (Private IP only)
```

### 8.2 Secrets Management

- All credentials in **Google Cloud Secret Manager**
- No secrets in environment variables, code, or config files
- Secret rotation enforced every 90 days for service accounts
- Secret access is logged and audited

### 8.3 Container Security

- Base images: `distroless` (Python), `rust:slim` (Rust)
- No root processes in containers
- Read-only root filesystem where possible
- SBOM generated for every release (Syft / Grype)
- Trivy vulnerability scan as CI gate (block on HIGH/CRITICAL)

### 8.4 Dependency Security

**Python stack:**
- `pip-audit` in CI pipeline
- `safety` check on every dependency update
- Pinned dependencies with hash verification (`requirements.txt` with `--hash`)

**Rust stack:**
- `cargo audit` in CI pipeline
- `cargo deny` for license and security policy
- Known issue: spectre library causes Windows build failures in 2 crates (tracked, not a production risk)

---

## 9. Incident Response

### 9.1 Security Incident Classification

| Severity | Definition | Response Time |
|---|---|---|
| P0 | Guardian bypassed, audit trail tampered, causal poisoning confirmed | Immediate (< 15 min) |
| P1 | Unauthorized semantic layer write, PII exposure, privilege escalation | < 1 hour |
| P2 | Anomalous agent behavior, prompt injection attempt, dependency vulnerability | < 4 hours |
| P3 | Failed auth spike, drift in security metric, policy misconfiguration | < 24 hours |

### 9.2 Detection Sources

- Cloud Armor WAF alerts
- Audit trail anomaly detection (statistical deviation from baseline write patterns)
- Guardian verdict anomaly (unusual REJECTED rate → possible evasion attempt)
- H-Neuron hallucination score spike across agent pool
- Secret Manager access anomaly (unexpected secret reads)

---

## 10. Security Testing Requirements

All platform components must pass these checks before promotion to any non-development environment:

- [ ] SAST: Bandit (Python), `cargo clippy` with security lints (Rust)
- [ ] Dependency audit: `pip-audit`, `cargo audit`
- [ ] Container scan: Trivy (block on HIGH/CRITICAL)
- [ ] DAST: OWASP ZAP against staging API (weekly)
- [ ] Guardian bypass test: verified that every semantic write path enforces verdict check
- [ ] Audit trail integrity test: verified that no audit entry can be updated or deleted
- [ ] Prompt injection test suite: structured adversarial input set for all LLM-facing endpoints
- [ ] Causal poisoning simulation: synthetic adversarial edge injection detected and rejected

---

## 11. Security Contact and Escalation

Security findings, suspected causal poisoning attacks, or governance violations should be escalated through the Governance Officer role in the platform. For infrastructure-level incidents, use the P0/P1 incident response path. Do not attempt to resolve security incidents autonomously via agent actions.
