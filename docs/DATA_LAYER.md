# Data Layer, Semantic Layer & Integrations

> **Scope:** Canonical specification for all data storage, causal semantic objects, integration patterns, and event streaming in the Unified Enterprise Causal Platform. All platform components must read and write data through the interfaces defined here.
>
> **Status (read against the code 2026-09-25):** §2 (the object model) matches
> `backend/src/semantic_layer/models.py`. Every other section says what exists, or is marked as a
> target design with its register id in
> [`conformance/declared_not_implemented.json`](../conformance/declared_not_implemented.json).
> The targets that were never built or measured (business-intelligence connectors, the benchmark
> table) are in [`history/DATA_LAYER-targets-2026-09-25.md`](history/DATA_LAYER-targets-2026-09-25.md).

---

## 1. Architecture Overview

> **Status:** target design. Kafka and Neo4j are not in use (registers `kafka-streaming`,
> `neo4j-graph-store`), and no connector is called by the running platform. Today, data enters
> through the REST API and MCP tools. The semantic layer is the SQL store of §3.2, and
> estimation runs in `packages/causal-core`.

```
External Sources ──► Connectors ──► Ingestion Bus (Kafka)
                                          │
                                   ┌──────▼──────┐
                                   │  Raw Event   │
                                   │   Store      │
                                   └──────┬──────┘
                                          │ Transform + Enrich
                                   ┌──────▼──────────────────┐
                                   │  Causal Semantic Layer   │  ← System of Record
                                   │  (PostgreSQL + Neo4j)    │
                                   └──────┬──────────────────┘
                          ┌───────────────┼───────────────┐
                     ┌────▼────┐    ┌─────▼──────┐  ┌─────▼──────┐
                     │  Graph  │    │ Assumption  │  │  Evidence  │
                     │Registry │    │  Registry   │  │  Registry  │
                     └────┬────┘    └─────┬──────┘  └─────┬──────┘
                          └──────────────┬───────────────┘
                                   ┌─────▼──────┐
                                   │  Runtime   │
                                   │  & Agents  │
                                   └─────┬──────┘
                          ┌──────────────┼──────────────┐
                     ┌────▼────┐   ┌─────▼─────┐  ┌────▼────┐
                     │CausalOps│   │Simulation │  │Vertical │
                     │ Control │   │  Cockpit  │  │ Agents  │
                     └─────────┘   └───────────┘  └─────────┘
```

---

## 2. Causal Semantic Layer: Object Model

The Causal Semantic Layer is the **governed system of record** for all causal knowledge in the platform. It is not a data lake or a feature store. It is an ontology of cause-and-effect relationships over business entities.

### 2.1 Core Object Types

#### `CausalEntity`
A business-meaningful unit that can cause or be caused by something.

```typescript
interface CausalEntity {
  id: UUID;
  name: string;                           // "customer", "service", "supplier"
  domain: string;                         // "devops", "marketing", "supply_chain"
  attributes: Record<string, AttributeDef>;
  ontologyVersion: string;
  createdBy: UserRef;
  approvedAt: ISO8601 | null;
  approvedBy: UserRef | null;
  lineage: LineageRecord[];
}
```

**Standard entity types:** `SERVICE`, `CUSTOMER`, `SUPPLIER`, `CAMPAIGN`, `ACCOUNT`, `INCIDENT`, `DEPLOYMENT`, `FEATURE`, `EMPLOYEE`

---

#### `Treatment`
A variable that represents an action or intervention that can be applied.

```typescript
interface Treatment {
  id: UUID;
  name: string;
  entityRef: CausalEntity["id"];
  treatmentType: "BINARY" | "CONTINUOUS" | "MULTI_VALUED";
  units: string | null;
  feasibilityConstraints: Constraint[];
  costModel: CostModel | null;
  ownerTeam: string;
}
```

---

#### `Outcome`
The effect variable whose causal drivers we want to understand.

```typescript
interface Outcome {
  id: UUID;
  name: string;
  entityRef: CausalEntity["id"];
  measurementType: "COUNT" | "RATE" | "DURATION" | "BINARY" | "CONTINUOUS";
  timeHorizon: Duration;
  businessValue: "HIGH" | "MEDIUM" | "LOW";
  sloRef: string | null;
}
```

---

#### `Confounder`
A variable that causally influences both a treatment and an outcome, creating spurious associations.

```typescript
interface Confounder {
  id: UUID;
  name: string;
  treatmentRef: Treatment["id"];
  outcomeRef: Outcome["id"];
  measuredStatus: "OBSERVED" | "PARTIALLY_OBSERVED" | "LATENT";
  proxyVariables: string[];
  evidenceStrength: "STRONG" | "MODERATE" | "WEAK" | "ASSUMED";
}
```

---

#### `Mediator`
A variable on the causal path between treatment and outcome.

```typescript
interface Mediator {
  id: UUID;
  name: string;
  treatmentRef: Treatment["id"];
  outcomeRef: Outcome["id"];
  mediationType: "FULL" | "PARTIAL";
}
```

---

#### `CausalEdge`
A directed, typed, evidenced causal relationship between two semantic objects.

```typescript
interface CausalEdge {
  id: UUID;
  source: CausalObjectRef;
  target: CausalObjectRef;
  edgeType: "CAUSES" | "MEDIATES" | "CONFOUNDS" | "MODERATES" | "BLOCKS";
  direction: "FORWARD" | "BACKWARD" | "BIDIRECTIONAL_FORBIDDEN";
  confidence: number;                   // 0.0 – 1.0
  evidenceRefs: Evidence["id"][];
  assumptionRefs: Assumption["id"][];
  approvalStatus: "DRAFT" | "UNDER_REVIEW" | "APPROVED" | "DEPRECATED";
  approvedBy: UserRef | null;
  approvedAt: ISO8601 | null;
  guardianVerdictRef: AuditEntry["id"];
  version: string;
  lineage: LineageRecord[];
}
```

---

#### `GraphFragment`
A named, versioned, reusable sub-DAG over a set of semantic objects.

```typescript
interface GraphFragment {
  id: UUID;
  name: string;
  domain: string;
  description: string;
  edges: CausalEdge["id"][];
  entryNodes: CausalObjectRef[];
  exitNodes: CausalObjectRef[];
  approvalStatus: "DRAFT" | "APPROVED" | "DEPRECATED";
  version: string;
  usageCount: number;
  tags: string[];
}
```

---

#### `Assumption`
An explicit statement about the causal structure that cannot be derived from data alone.

```typescript
interface Assumption {
  id: UUID;
  statement: string;
  assumptionType: "IGNORABILITY" | "POSITIVITY" | "SUTVA" | "EXCLUSION_RESTRICTION" | "STRUCTURAL" | "DOMAIN";
  scope: CausalObjectRef[];
  justification: string;
  elicitedFrom: "DOMAIN_EXPERT" | "AGENT" | "LITERATURE" | "PRIOR_STUDY";
  elicitedBy: UserRef;
  reviewStatus: "PENDING" | "ACCEPTED" | "CONTESTED" | "REJECTED";
  sensitivityResult: SensitivityResult | null;
}
```

---

#### `Intervention`
A logged, governed action taken in the real world with causal intent.

```typescript
interface Intervention {
  id: UUID;
  name: string;
  treatmentRef: Treatment["id"];
  targetEntityRef: CausalEntity["id"];
  initiatedBy: UserRef;
  initiatedAt: ISO8601;
  approvedBy: UserRef;
  approvalTimestamp: ISO8601;
  guardianVerdictRef: AuditEntry["id"];
  expectedEffect: EffectEstimate;
  actualEffect: EffectMeasurement | null;       // Filled after outcome observed
  outcomeObservedAt: ISO8601 | null;
  feedbackLoopClosed: boolean;
}
```

---

#### `Evidence`
A citation linking a causal claim to a supporting source.

```typescript
interface Evidence {
  id: UUID;
  evidenceType: "STATISTICAL_TEST" | "DOCUMENT" | "EXPERT_KNOWLEDGE" | "EXPERIMENT" | "SIMULATION";
  sourceRef: string;                            // File path, URL, metric ID
  snippet: string;
  statisticalResult: StatisticalResult | null;
  relevanceScore: number;
  createdBy: UserRef | agentId;
  createdAt: ISO8601;
}
```

---

### 2.2 Semantic Layer Registry Rules

These rules are what `backend/src/semantic_layer/registry.py` is meant to hold. Each carries
its state today.

1. **All writes require a Guardian `APPROVED` verdict.** Holds for the registry's write methods.
   `propose_draft` and `promote_to_approved` refuse a verdict that does not exist, is not
   APPROVED, is unsigned, names a different object, or has already been spent, and
   `promote_to_approved` also refuses one not issued for action `GOVERNANCE_APPROVE`
   (`septa-60`, `backend/tests/guardian/test_approve_action_binding.py`). `reject_draft`
   applies the same checks to a REJECTED verdict. See
   `backend/tests/guardian/test_verdict_binding.py`.
2. **All objects are versioned.** Does not hold. Objects carry a `version` field (default
   `1.0.0`), but approval and rejection `UPDATE` the row in place, changing its status, approver
   and lineage, and no code path ever increments `version`. Register:
   `semantic-objects-mutated-in-place`.
3. **All approved objects have lineage.** Partial. Approval appends one lineage step, and the
   verdict reference is set. See §6 and register `lineage-partial`.
4. **Draft objects do not participate in simulations.** Holds: SimulationAgent refuses a fragment
   that is not APPROVED.
5. **Deprecated objects are retained.** Holds: nothing deletes a semantic object. Rejection is
   stored as `DEPRECATED`, because `ApprovalStatus` has no `REJECTED` member.

---

## 3. Storage Architecture

**What runs today:** one relational database, reached through `CloudSqlFactory`
(`backend/src/db/factory.py`). It is SQLite when `ENV=local` and PostgreSQL otherwise. The causal
graph lives in its tables (`causal_edges`, `graph_fragments`). §3.1 (Neo4j) and §3.3 (Kafka)
are target designs.

### 3.1 Neo4j (Graph Store)

> **Status:** target design, not implemented. See register `neo4j-graph-store`. The causal graph
> is stored in SQL. The one Neo4j code path is an optional experience-buffer backend
> (`skills/experience_buffer_neo4j.py`, used when `NEO4J_URI` is set), and the tests exercise it
> only with mocks.

**Used for:**
- Causal graph storage: nodes (CausalEntity, Treatment, Outcome, Confounder) and edges (CausalEdge)
- GraphFragment registry
- Experience buffer (past incidents and resolutions as graph patterns)
- d-separation and backdoor path queries

**Schema conventions:**
- Node labels: `:CausalEntity`, `:Treatment`, `:Outcome`, `:Confounder`, `:GraphFragment`
- Relationship types: `:CAUSES`, `:CONFOUNDS`, `:MEDIATES`, `:PART_OF_FRAGMENT`, `:SIMILAR_TO`
- All nodes carry: `id`, `domain`, `version`, `approvalStatus`, `createdAt`

**Query patterns:**
```cypher
-- Find all confounders on path between treatment T and outcome O
MATCH path = (t:Treatment {id: $treatmentId})-[*]-(o:Outcome {id: $outcomeId})
WHERE ALL(n IN nodes(path) WHERE n.approvalStatus = 'APPROVED')
RETURN path

-- Retrieve domain-specific graph fragment
MATCH (f:GraphFragment {domain: $domain, approvalStatus: 'APPROVED'})
MATCH (f)-[:CONTAINS_EDGE]->(e:CausalEdge)
RETURN f, collect(e) AS edges ORDER BY f.version DESC LIMIT 1
```

**Benchmark target (never measured):** Neo4j 5.x, 3-node cluster for production. d-separation query p95 < 50ms on graphs with ≤ 500 nodes.

---

### 3.2 Relational Store (PostgreSQL, SQLite locally)

There are two schemas. They create the same tables with the same columns, and
`backend/tests/integration/test_postgres_schema_parity.py` holds them to that. Column types,
grants, row-level security and most `CHECK` constraints exist only on the PostgreSQL side
(SQLite keeps three: `audit_log.guardian_verdict`, `decision_followups.kind` and
`causal_model_versions.version`).

- **PostgreSQL**: `backend/src/db/migrations/001_initial.sql` to `010_rls_binds.sql`, applied in
  filename order. Two things in this repository apply them: `docker-compose.yml`, whose init
  script (`backend/src/db/docker/initdb.sh`) runs them when the container's volume is first
  created and then gives `causal_app` its local-dev login, and the test fixtures when
  `TEST_DATABASE_URL` is set (`backend/tests/conftest.py`, which builds a fresh database from
  them for the run; the gate's `backend-pytest-postgres` step and CI do this). No deploy step
  runs them. They are run as a superuser (001 creates roles); the backend then connects as
  `causal_app`, not as the owner.
- **SQLite**: the `SQLITE_DDL` string in `backend/src/db/factory.py`, created by
  `CloudSqlFactory.init_db()` on a SQLite engine (the choice is by the engine's dialect, never
  run on PostgreSQL). The default test run, and the gate's `backend-pytest` step, use it.

| Table | PostgreSQL | SQLite | Holds |
|---|---|---|---|
| `audit_log` | 001, 002 | yes | Every Guardian verdict and agent action record. Append-only |
| `verdict_consumption` | 002 | yes | One row per spent verdict. Its primary key is what makes a verdict single-use |
| `causal_entities`, `causal_edges`, `graph_fragments`, `assumptions` | 001 (+002 for `causal_entities.guardian_verdict_ref`, +006 for `graph_fragments.lineage`) | yes | The semantic layer objects of §2 |
| `interventions` | 001 | yes | Intervention log: expected vs actual effect, feedback closure |
| `decision_records` | 003 | yes | The decision ledger's hash chain, one sealed record per row. Append-only |
| `decision_followups` | 004, 005 | yes | The follow-up plan written at decision time, and each later realization. Append-only; 005 allows one row per decision and kind, so a decision is measured once |
| `causal_model_versions`, `decision_model_refs` | 007 | yes | The assumption registry: immutable model versions (content-addressed, each naming its parent), and which decision used which version. Append-only |
| `session_state` | 008 | yes | Latest `EpistemicState` snapshot per session (`orchestration/session_store.py`), upserted on `session_id`; also what the incident triage agent's experience buffer reads back |
| (row-level security) | 001, replaced by 010 | no | Not a table: 010 puts a domain policy on `causal_entities`, `causal_edges` and `graph_fragments`, fed by the settings `backend/src/db/scope.py` sets on every transaction (docs/CYBERSECURITY.md §4.2), and grants `causal_app` the sequence 004 created after 001's grant |
| `incidents` | 009 | yes | Incident records (`api/incidents.py`), upserted on `id` with `INSERT ... ON CONFLICT`, keeping the first `created_at`. Decisions on their interventions are `audit_log` rows, joined in on read |

Until 008 and 009 (septa-52) `session_state` and `incidents` existed only in the SQLite schema,
and on PostgreSQL session persistence and the incidents API failed with a missing table. Neither
schema has an evidence, user-preference or simulation-result table. Evidence is referenced by id
from `causal_edges.evidence_refs`, and simulation results live on `EpistemicState` (so inside the
`session_state` snapshot).

**Binding values.** SQLite stores whatever it is given; asyncpg refuses a JSON string into a
`TEXT[]` or `UUID[]` column, an ISO string into a `TIMESTAMPTZ`, and an integer into a
`BOOLEAN`. The code bound all three shapes, and until the suite also ran on PostgreSQL the
Guardian could not write `audit_log` there. Statements now declare those parameters with the
types in `backend/src/db/types.py` (`typed_text(sql, created_at=TIMESTAMP, rules=TEXT_ARRAY)`),
which bind a `datetime`, a list or a `bool` on PostgreSQL and the same text as before on SQLite,
and read back through `timestamp_text` (a `TIMESTAMPTZ` and SQLite's ISO text render alike, so a
signature over the text verifies on either) and `json_value` (`JSONB` arrives decoded). `JSONB`
and `UUID` columns take a string parameter as is.

**`audit_log`** (PostgreSQL after migration 002):

```sql
CREATE TABLE audit_log (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id            TEXT        NOT NULL,
    action_type         TEXT        NOT NULL,
    payload_hash        TEXT        NOT NULL,
    guardian_verdict    TEXT        NOT NULL
        CHECK (guardian_verdict IN ('APPROVED', 'REJECTED', 'REQUIRES_ESCALATION', 'PENDING')),
    policy_rules_cited  TEXT[]      NOT NULL DEFAULT '{}',
    session_id          TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    subject_id          UUID,   -- 002: the object this verdict authorises; NULL authorises none
    signature           TEXT,   -- 002: HMAC-SHA256 by the Guardian; NULL = history, not authority
    notes               TEXT    -- 002: approval notes / rejection reason, unsigned
);
```

On PostgreSQL, `audit_log`, `decision_records`, `decision_followups` and the 007 tables are append-only in two
ways: `UPDATE` and `DELETE` are revoked from the application roles, and `BEFORE UPDATE/DELETE`
triggers raise. The SQLite schema declares the same triggers.

**Roles.** 001 creates `causal_app` (what the backend connects as), `causal_governance` and
`causal_readonly`, none of them owners. `causal_app` may `SELECT` and `INSERT` on every table,
`UPDATE` those that are not append-only, and use the sequences; it has no `DELETE` or `TRUNCATE`
anywhere. Row-level security binds it on the three semantic tables above; the backend refuses to
start on PostgreSQL as a role it does not bind (a superuser, `BYPASSRLS`, or the owner).

**`interventions`** (001) has no foreign keys to treatment or user tables, because none exist.
`treatment_ref` and `target_entity_ref` are loose `UUID`s, `initiated_by` and `approved_by` are
`UserRef` JSON (`JSONB`), and `guardian_verdict_ref` is `NOT NULL REFERENCES audit_log(id)`.

**Cloud deployment:** `CloudSqlFactory` can connect to Cloud SQL through the Unix socket named by
`CLOUD_SQL_INSTANCE_CONNECTION_NAME`, or to any PostgreSQL through `DATABASE_URL` (or a file named by `DATABASE_URL_FILE`),
in either case as `causal_app` or a member of it that owns nothing (the socket path's `DB_USER`
must be such a role). This repository contains no Cloud Run deployment of it.

---

### 3.3 Kafka (Event Streaming)

> **Status:** target design, not implemented. See register `kafka-streaming`. No Kafka client is a
> dependency. `docker-compose.yml` has a broker (and a Kafka UI) behind its `future` profile, and no platform code connects to it.

**Used for:**
- Real-time operational data ingestion (metrics, alerts, change events)
- Intervention event emission
- Drift alert propagation
- Cross-service decoupling

**Topic naming convention:** `{domain}.{entity}.{event_type}.{version}`

Examples:
- `devops.incident.created.v1`
- `devops.deployment.completed.v1`
- `platform.graph.edge_proposed.v1`
- `platform.governance.verdict_issued.v1`
- `marketing.campaign.activated.v1`

**Consumer groups:**
- `causal-discovery-consumer` → triggers CausalDiscoveryAgent on new event patterns
- `drift-monitor-consumer` → feeds DriftMonitorAgent with live distribution data
- `audit-consumer` → appends all governance events to audit trail

**Benchmark target (never measured):** p95 event latency < 100ms end-to-end from source connector to consumer.

---

### 3.4 The factory switch

Code never opens a database connection directly. It asks `CloudSqlFactory` for a session, and
the factory picks SQLite or PostgreSQL from `ENV`. The two schemas behind it have the same
tables and columns but not the same types, so the switch is transparent only to code that binds
through `backend/src/db/types.py` (§3.2); the gate runs the backend suite on both.

---

## 4. Connector Library

### 4.1 DevOps Connectors

| System | Integration Type | Data Pulled | Status |
|---|---|---|---|
| Prometheus | Pull (HTTP API) | Metrics time series, alert states | Class implemented and unit-tested with mocked HTTP (`src/connectors/prometheus.py`, `tests/unit/test_connectors.py`). Nothing in the running platform instantiates it |
| Alertmanager | Push webhook | Alert payloads, severity, labels | planned |
| PagerDuty | REST API + webhook | Incidents, escalation events | planned |
| OpsGenie | REST API | On-call schedules, incident data | planned |
| GitHub / GitLab | Webhook + REST | Deployment events, PR metadata | planned |
| Datadog | REST API | APM traces, dashboards | planned |
| Kubernetes | Kubernetes API | Pod events, resource state changes | planned |

Business-intelligence connectors (Salesforce, Snowflake, dbt, ...) were listed here as a Phase 2-3
target; none was built, and the list is in
[`history/DATA_LAYER-targets-2026-09-25.md`](history/DATA_LAYER-targets-2026-09-25.md).

### 4.2 Connector Interface Contract

All connectors subclass `BaseCausalConnector` (`backend/src/connectors/base.py`), which is the
Python form of this interface:

```typescript
interface CausalConnector {
  id: string;
  domain: string;
  version: string;

  // Fetch latest events since cursor
  fetchEvents(cursor: Cursor): Promise<DomainEvent[]>;

  // Publish to a Kafka topic -- a no-op stub in BaseCausalConnector (no Kafka client exists)
  subscribe(topic: KafkaTopic): void;

  // Map raw events to semantic layer objects
  mapToSemanticObjects(events: DomainEvent[]): CausalSemanticDraft[];

  // Validate that required data fields are present
  validate(): ConnectorHealthResult;
}
```

`map_to_semantic_objects` produces `DRAFT` objects. The intended route is CausalDiscoveryAgent,
then the Guardian, then human approval. No code path calls a connector today, so nothing takes
that route yet.

---

## 5. Semantic Drift and Data Quality

> **Status:** the drift functions exist; the monitoring pipeline gives them no data. See register
> `drift-monitor-no-data`.

### 5.1 Drift Detection Pipeline

This is what `DriftMonitorAgent.process` does (`backend/src/agents/drift_monitor.py`):

```
fragment id → drift_detect (KL) → causal_structure_compare → semantic_drift_detect
                                                                   │
                                   calculate_drift_severity (concept_drift=False)
                                                                   │
      KL > 0.1: alert via the in-process AlertEmitter; KL > 0.5 (severe): alert + human review
```

The thresholds are `_DEFAULT_KL_THRESHOLD = 0.1` and `_DEFAULT_SEVERE_KL_THRESHOLD = 0.5`.
The agent calls each skill with the fragment id only, and each skill returns "no drift" when
it gets no data: `drift_detect` answers `kl_divergence 0.0` with `note: no_data_for_comparison`,
and the other two answer `no_fragment_edges` and `no_definitions_for_comparison`. So the periodic
sensor (`backend/src/sensors/periodic_drift.py`), which runs this agent, cannot raise a drift
alert today. Alerts go to `AlertEmitter` (logging, callbacks, deduplication). The
`platform.drift.alert.v1` topic name in the agent's return value is only a label: no Kafka
producer exists (register `kafka-streaming`).

### 5.2 Drift Types

The functions below are in `backend/src/skills/drift_detect.py`. They compute their result when
called directly with data, and the unit tests call them that way.

| Type | What the function computes | Wired into the agent? |
|---|---|---|
| **Distribution drift** | KL divergence (and Jensen-Shannon) between per-feature histograms | Called, with no data |
| **Causal drift** (`detect_causal_drift`) | Flags an edge when a supplied *correlation* for it falls below 0.5. That is correlation, not a causal test | Called, with no edges |
| **Semantic drift** (`detect_semantic_drift`) | Jaccard word overlap between two entity definitions, drift below 0.7 | Called, with no definitions |
| **Concept drift** (`detect_concept_drift`) | Chi-squared test on two label distributions | Not called; the agent passes `concept_drift=False` |

None of the drift types triggers re-estimation, a sensitivity re-test or a fragment re-review.

---

## 6. Lineage and Provenance

> **Status:** partial -- see register `lineage-partial`.

The `causal_edges` and `causal_entities` tables carry a `lineage` column (a JSON list), and the
models declare it as `list[LineageRecord]`. `graph_fragments` gained the same column in
`006_graph_fragment_lineage.sql` (and in the SQLite DDL). This is what actually gets written:

- for an edge, the `lineage` list the submitter sent with the draft (`propose_draft` stores it
  as given); an entity's draft lineage is not stored, and the column keeps its `[]` default;
- one entry per human decision: `promote_to_approved` appends `{step_type: "APPROVED", actor_id,
  timestamp, description}` naming the verdict id, and `reject_draft` appends a `REJECTED` step.
  Both read `lineage` from the object's table first; before 006 that read failed for a
  `GraphFragment`, so no fragment could be approved or rejected.

Nothing records the original data source, the transformation steps (agent, skill, version), or
the policy rules applied into `lineage`. The Guardian verdict and the rules it cited are in
`audit_log`, linked from the row by `guardian_verdict_ref`, and the evidence and assumptions are
linked by id (`evidence_refs`, `assumption_refs`). So the chain can be partly rebuilt by joining
those, but no single field holds it. That is a step towards the transparency the EU AI Act asks
for (Art. 13). It is not evidence of meeting it, and nothing here reproduces an analysis from its
lineage.

---

## 7. Data Access Patterns by Component

| Component | Read | Write | Notes |
|---|---|---|---|
| CausalDiscoveryAgent | Semantic layer (read) | `audit_write` records (PENDING, unsigned) | Proposes; does not persist semantic objects itself |
| GovernanceAgent | The action payload and `EpistemicState` it is given | `audit_log` (every verdict, signed) | Its verdict is what `propose_draft` / `promote_to_approved` / `reject_draft` require |
| SimulationAgent | APPROVED graph fragments | `EpistemicState.simulation_results` (in memory; the `session_state` snapshot) | Read-only on the semantic layer |
| DriftMonitorAgent | Whatever its skills are handed (see §5) | In-process `AlertEmitter` | No semantic-layer writes; no Kafka |
| `GET /api/semantic/*` | Entities, edges, fragments inside the user's domain scope; APPROVED only for a `viewer` | -- | Outside the scope is 404 (CYBERSECURITY.md §4.2) |
| `POST /api/semantic/drafts` | -- | `causal_edges` / `graph_fragments` / `causal_entities` / `assumptions` as DRAFT | A drafting role, a domain inside the user's scope, then a GovernanceAgent check; status forced to DRAFT |
| `POST /api/governance/approve/{id}`, `/reject/{id}` | -- | Status, approver and lineage on the same row | A governance role, the object inside the reviewer's domains; see §2.2 rule 2 |
| `POST /api/v1/decisions` | A registered model version, when the request names one | `decision_records`, `decision_followups`, and `decision_model_refs` when a model is named | Append-only hash chain |
| `POST /api/v1/models`, `/{name}/versions`, `/{name}/reviews` | The model's head | `causal_model_versions` | Append-only; a version is appended only to the head it was built on; proposing needs a drafting role, reviews a governance role |

The DAG Workbench is planned, not built (register `dag-workbench`).
