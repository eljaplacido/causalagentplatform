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

What runs today:

```
REST API / MCP tools ──► Causal Semantic Layer (PostgreSQL, SQLite locally)  ← system of record
                                │   graph fragments, edges, entities, assumptions (§3.2)
                                ▼
                          Runtime & agents ──► packages/causal-core (identification, estimation)
                                │
                                ├──► audit_log, decision ledger (same database)
                                └──► optional Neo4j (NEO4J_URI): experience buffer, drift alerts
```

Connectors (§4) exist as classes, and no running code path calls one. There is no event bus.
Earlier versions of this page drew an ingestion bus on Kafka and a semantic layer on PostgreSQL
plus Neo4j; neither was built, and those claims were withdrawn (`kafka-streaming` and
`neo4j-graph-store` in the register's `left_the_list`). The withdrawn text is in git history
(`f64b2b0`).

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
  revision: number;                     // server-kept: 1 at the draft, +1 per recorded change (014)
  sourceData: DataSource | null;        // the data it was drafted from; approval needs one
}

interface DataSource {                  // declared by the submitter, not checked against the data
  kind: string;                         // "dataset" by default
  ref: string;                          // where the data is
  digest: string;                       // "sha256:<64 hex>": what it was
  description: string | null;
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
  createdBy: UserRef | null;            // who drafted it; null before migration 012
  revision: number;                     // server-kept: 1 at the draft, +1 per recorded change (014)
  sourceData: DataSource | null;        // the data it was drafted from; approval needs one
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
  revision: number;                     // server-kept: 1 at the draft, +1 per recorded change (014)
  sourceData: DataSource | null;        // the data it was drafted from; approval needs one
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
  revision: number;                     // server-kept: 1 at the draft, +1 per recorded change (014)
  sourceData: DataSource | null;        // the data it was drafted from; approval needs one
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
   applies the same checks, and the verdict must have been issued for action
   `GOVERNANCE_REJECT`; a verdict for `GOVERNANCE_REJECT` or `GOVERNANCE_APPROVE` authorises
   that decision only (`septa-53`). `record_edge_sensitivity` needs one issued for
   `RECORD_SENSITIVITY` on the edge (`septa-62`). See `backend/tests/guardian/test_verdict_binding.py`.
2. **All objects are versioned.** Holds for entities, edges, fragments and assumptions. Every
   state is an immutable row in `semantic_object_versions` (`014_semantic_object_versions.sql`):
   the object's complete row at that revision, what changed it, who, and on which verdict. The
   object's own row is the current version; approval, rejection and recording a sensitivity
   result on a draft edge record the next revision and never rewrite one, and the database
   refuses a head update that skips that
   (ADR-001 §4; `backend/tests/unit/test_registry_versioning.py`). Interventions are not
   versioned yet (register `interventions-mutated-in-place`).
3. **All approved objects have lineage.** Holds from source data to approver: approval refuses
   an object whose source data or drafter is not on record, and
   `GET /api/semantic/objects/{type}/{id}/lineage` serves the chain (§6;
   `backend/tests/integration/test_lineage_chain.py`). The onward link, to the analyses that used
   an object, is register `lineage-to-analyses`.
4. **Draft objects do not participate in simulations.** Holds: SimulationAgent refuses a fragment
   that is not APPROVED.
5. **Deprecated objects are retained.** Holds: nothing deletes a semantic object. Rejection is
   stored as `DEPRECATED`, because `ApprovalStatus` has no `REJECTED` member.

---

## 3. Storage Architecture

**What runs today:** one relational database, reached through `CloudSqlFactory`
(`backend/src/db/factory.py`). It is SQLite when `ENV=local` and PostgreSQL otherwise. The causal
graph lives in its tables (`causal_edges`, `graph_fragments`), and every graph query (acyclicity,
d-separation, back-door and front-door identification) runs in Python over those rows, in
`packages/causal-core`. Neo4j (§3.1) is an optional side store for two things that are not the
graph. There is no event streaming (§3.3).

### 3.1 Neo4j (optional side store)

Neo4j is **not** the causal graph store, and no d-separation or path query runs in it; that
claim was withdrawn (register `neo4j-graph-store`, in `left_the_list`). When `NEO4J_URI` is set,
`Neo4jFactory` (`backend/src/db/neo4j_factory.py`) opens a driver and two optional paths use it:

- **Experience buffer** (`backend/src/skills/experience_buffer_neo4j.py`): `(:Incident)` nodes
  with a 90-day TTL (`EXPERIENCE_BUFFER_TTL_DAYS`) and a 30-day relevance half-life
  (`EXPERIENCE_BUFFER_DECAY_HALF_LIFE`). Without Neo4j, `experience_buffer.py` reads the SQL
  `session_state` snapshots instead, and storing an incident returns `stored: false`.
- **Drift alerts** (`backend/src/skills/drift_detect.py`): `(:DriftAlert)` nodes with a TTL and an
  acknowledged flag. Without Neo4j, alerts are emitted (§5.1) but not stored.

Whether that optional Neo4j answers is reported by the authenticated
`GET /api/v1/platform/connectors` (connector `graph-neo4j`): `unconfigured` (no `NEO4J_URI`;
healthy, since the SQL fallback serves), `reachable`, or `unreachable` (no driver, a failed
connectivity check, or no answer within 3 s), with a fixed message instead of the driver's error
text. Tested with a mocked driver (`backend/tests/integration/test_platform_api.py`). (An unmounted `/health/neo4j` router that
checked the same was deleted on 2026-09-26.) Both paths are tested only against a mocked driver (`backend/tests/unit/test_experience_buffer_neo4j.py`,
`test_drift_detect.py`, `test_neo4j_factory.py`); no test runs them against a real Neo4j.
`docker-compose.yml` starts a local Neo4j 5 for them.

---

### 3.2 Relational Store (PostgreSQL, SQLite locally)

There are two schemas. They create the same tables with the same columns, and
`backend/tests/integration/test_postgres_schema_parity.py` holds them to that. Column types,
grants, row-level security and most `CHECK` constraints exist only on the PostgreSQL side
(SQLite keeps three: `audit_log.guardian_verdict`, `decision_followups.kind` and
`causal_model_versions.version`).

- **PostgreSQL**: `backend/src/db/migrations/001_initial.sql` to `013_assumption_sensitivity_verdict.sql`, applied in
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
| `causal_entities`, `causal_edges`, `graph_fragments`, `assumptions` | 001 (+002 for `causal_entities.guardian_verdict_ref`, +006 for `graph_fragments.lineage`, +012 for `causal_edges.created_by`, +013 for `assumptions.sensitivity_verdict_id`, +014 for `revision` and `source_data`) | yes | The semantic layer objects of §2: each row the current version of its object. A row changes only by advancing `revision` by one over a recorded revision (014's triggers, on both engines) |
| `semantic_object_versions` | 014 | yes | Every version of every semantic object: its complete row at a revision (`state`), the change (`PROPOSED`, `APPROVED`, `REJECTED`, `RECORDED`), actor, agent and platform version, and verdict. Append-only; domain-scoped by RLS on PostgreSQL. 014 recorded each existing row as revision 1 |
| `interventions` | 001 | yes | Intervention log: expected vs actual effect, feedback closure |
| `decision_records` | 003 | yes | The decision ledger's hash chain, one sealed record per row. Append-only |
| `decision_followups` | 004, 005 | yes | The follow-up plan written at decision time, and each later realization. Append-only; 005 allows one row per decision and kind, so a decision is measured once |
| `causal_model_versions`, `decision_model_refs` | 007 | yes | The assumption registry: immutable model versions (content-addressed, each naming its parent), and which decision used which version. Append-only |
| `session_state` | 008 | yes | Latest `EpistemicState` snapshot per session (`orchestration/session_store.py`), upserted on `session_id`; also what the incident triage agent's experience buffer reads back |
| (row-level security) | 001, replaced by 010 (+014) | no | Not a table: 010 puts a domain policy on `causal_entities`, `causal_edges` and `graph_fragments` (014 adds `semantic_object_versions`), fed by the settings `backend/src/db/scope.py` sets on every transaction (docs/CYBERSECURITY.md §4.2), and grants `causal_app` the sequence 004 created after 001's grant |
| `incidents` | 009 | yes | Incident records (`api/incidents.py`), upserted on `id` with `INSERT ... ON CONFLICT`, keeping the first `created_at`. Decisions on their interventions are `audit_log` rows, joined in on read |
| `provenance_entries`, `provenance_erasures` | 011 | yes | W3C PROV records and LLM invocations per session (`semantic_layer/provenance_store.py`, §6), and the tombstones that hide a session's records from the provenance API. Append-only; `causal_readonly` gets no `SELECT` on the entries |

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

On PostgreSQL, `audit_log`, `decision_records`, `decision_followups`, the 007 tables, the 011 tables
and `semantic_object_versions` (014) are append-only in two ways: `UPDATE` and `DELETE` are revoked
from the application roles, and `BEFORE UPDATE/DELETE` triggers raise. The semantic head tables
are updatable only by 014's revision rule. The SQLite schema declares the same triggers.

**Roles.** 001 creates `causal_app` (what the backend connects as), `causal_governance` and
`causal_readonly`, none of them owners. `causal_app` may `SELECT` and `INSERT` on every table,
`UPDATE` those that are not append-only, and use the sequences; it has no `DELETE` or `TRUNCATE`
anywhere. Row-level security binds it on the three semantic tables above and on
`semantic_object_versions`; the backend refuses to
start on PostgreSQL as a role it does not bind (a superuser, `BYPASSRLS`, or the owner).

**`interventions`** (001) has no foreign keys to treatment or user tables, because none exist.
`treatment_ref` and `target_entity_ref` are loose `UUID`s, `initiated_by` and `approved_by` are
`UserRef` JSON (`JSONB`), and `guardian_verdict_ref` is `NOT NULL REFERENCES audit_log(id)`.

**Cloud deployment:** `CloudSqlFactory` can connect to Cloud SQL through the Unix socket named by
`CLOUD_SQL_INSTANCE_CONNECTION_NAME`, or to any PostgreSQL through `DATABASE_URL` (or a file named by `DATABASE_URL_FILE`),
in either case as `causal_app` or a member of it that owns nothing (the socket path's `DB_USER`
must be such a role). This repository contains no Cloud Run deployment of it.

---

### 3.3 Event streaming

None. No message broker client is a dependency and no code publishes or consumes events. Drift
alerts go through the in-process `AlertEmitter` (§5.1). The Kafka design that stood here (topics,
consumer groups, a latency target) was never built and has been withdrawn (register
`kafka-streaming`, in `left_the_list`); `docker-compose.yml` no longer defines a broker.

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
and the other two answer `no_fragment_edges` and `no_definitions_for_comparison`. So the agent
cannot raise a drift alert today, and nothing runs it on a schedule: the periodic `DriftSensor`
that was meant to, never started by the app, was deleted in September 2026 rather than scheduled
to alert on nothing. Alerts go to `AlertEmitter` (`backend/src/sensors/alert_emit.py`: logging,
callbacks, deduplication). The agent's
return value names that channel; nothing publishes to a broker (§3.3). Until 2026-09-26 the
`alert_emit` skill, dispatched as a skill, never received the state, answered `emitted: true`
with a `platform.drift.alert.v1` topic name, and emitted nothing.

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

> **Status:** implemented from source data to approver (`backend/tests/integration/test_lineage_chain.py`,
> SQLite and PostgreSQL). Not linked onward to the analyses that used an object (register
> `lineage-to-analyses`).

`GET /api/semantic/objects/{type}/{id}/lineage` returns a `LineageChain` for an entity, edge,
fragment or assumption, assembled by `SemanticLayerRegistry.get_lineage` from what the platform
recorded -- the version history (§2.2 rule 2) and `audit_log` -- not from the `lineage` list the
submitter sent, which stays on the object as the submitter's own claim:

| Link | From | What it names |
|---|---|---|
| `sourceData` | The object's `source_data`, declared with the draft (`POST /api/semantic/drafts` payload `sourceData`) and carried in every version | The data it was drafted from: `kind`, `ref`, and a `sha256:` digest pinning its content |
| `draftedBy` | Revision 1 (`PROPOSED`) | The person the token named, the agent the draft came through (`api-draft-submission`), the platform version, the time |
| `verdicts` | `audit_log`: every verdict spent on a version, and every row issued for the object | Action, verdict, rules cited, the Guardian agent, the session, whether the signature verifies, the revision it was spent on; the unsigned human decision records with their notes |
| `approvedBy` | The `APPROVED` version | The approver, the time, the verdict |
| `steps` | The version history | Each revision: change, actor, agent, platform version, verdict |
| `provenanceSessions`, `provenanceRecords` | The verdicts' sessions; the provenance store | The provenance records held for those sessions |
| `gaps`, `complete` | -- | Every link that was never recorded, by name; `complete` only when there are none |

Approval requires the head of the chain: `promote_to_approved` refuses an object whose source
data or drafter is not on record (`LineageIncompleteError`, 422 from `/approve`, the verdict not
spent). An object approved before versioning keeps what its row says, attributed as such, and
its chain lists what is missing (no history, no source data, no approval verdict) rather than
inventing it.

Limits. The digest is the submitter's declaration: the platform does not read the data to check
it. Provenance records are read from the durable store (011, below) by session, as the
provenance API shows them: a record an erasure tombstone hides is not listed.
Nothing records which analyses, simulations or decisions used an object's versions (register
`lineage-to-analyses`). The chain is a record towards the transparency the EU AI Act asks for
(Art. 13); it is not evidence of meeting it.

**Provenance records** (ADR-002) are a separate, per-session store: `provenance_entries` and
`provenance_erasures` (011), read through `/api/v1/provenance/*` by governance roles. Session
runs, Guardian checks and `POST /api/v1/decisions` write them
(`backend/src/semantic_layer/provenance_capture.py`); what they hold about inputs is digests
(of a description, a dataset, a reasoning step's inputs and outputs), not the content. The store
is append-only; an erasure is a tombstone that hides a session's earlier records from the API and
keeps the rows, so it is not GDPR erasure (register `gdpr-controls`). LLM invocations are
recorded by the capture hook in `backend/src/agents/llm_factory.py`, one per call made through
`get_llm` (the narration callers, `backend/src/agents/narration.py`).

---

## 7. Data Access Patterns by Component

| Component | Read | Write | Notes |
|---|---|---|---|
| CausalDiscoveryAgent | Semantic layer (read) | `audit_write` records (PENDING, unsigned) | Proposes; does not persist semantic objects itself |
| GovernanceAgent | The action payload and `EpistemicState` it is given | `audit_log` (every verdict, signed) | Its verdict is what `propose_draft` / `promote_to_approved` / `reject_draft` / `record_edge_sensitivity` require |
| SimulationAgent | APPROVED graph fragments | `EpistemicState.simulation_results` (in memory; the `session_state` snapshot); given data, the kernel's sensitivity result on the fragment's draft Treatment -> Outcome edge (a new `assumptions` row, appended to the edge's `assumption_refs`) | That one write under a Guardian verdict for `RECORD_SENSITIVITY` on the edge; otherwise read-only on the semantic layer |
| DriftMonitorAgent | Whatever its skills are handed (see §5) | In-process `AlertEmitter`; `(:DriftAlert)` nodes in Neo4j when `NEO4J_URI` is set | No semantic-layer writes; no message broker |
| `GET /api/semantic/*` | Entities, edges, fragments inside the user's domain scope; APPROVED only for a `viewer`. `/objects/{type}/{id}/versions` and `/lineage` read the history and the chain (§6) under the same rule | -- | Outside the scope is 404 (CYBERSECURITY.md §4.2) |
| `POST /api/semantic/drafts` | -- | `causal_edges` / `graph_fragments` / `causal_entities` / `assumptions` as DRAFT, and its revision 1 in `semantic_object_versions` | A drafting role, a domain inside the user's scope, then a GovernanceAgent check in the named session's Cynefin domain or the caller's declared one (recorded with its source in the audit row's notes; nothing declared is DISORDER, refused by PR-006); status forced to DRAFT; an assumption's `sensitivity_verdict_id` is never written, so a drafted sensitivity result counts for nothing |
| `POST /api/semantic/edges/{id}/sensitivity` | The edge, inside the user's scope | A new IGNORABILITY `assumptions` row carrying the kernel's sensitivity result and the id of the verdict it was recorded under (`sensitivity_verdict_id`, migration 013: what makes PR-007 count it), recorded as its revision 1 (`PROPOSED`); the draft edge's next revision (`RECORDED`: its `assumption_refs` and lineage), with the version rows, in the same transaction | A drafting role; a Guardian verdict for `RECORD_SENSITIVITY` on the edge; never an APPROVED or DEPRECATED edge (409) |
| `POST /api/governance/approve/{id}`, `/reject/{id}` | -- | The next version: a `semantic_object_versions` row, and the head advanced to it | A governance role, the object inside the reviewer's domains; approval also needs source data and drafter on record; see §2.2 rules 2 and 3 |
| `POST /api/v1/decisions` | A registered model version, when the request names one | `decision_records`, `decision_followups`, `decision_model_refs` when a model is named, and `provenance_entries` (session `decision:<entry_digest>`) | Append-only hash chain |
| `POST /api/v1/session/run`, `/incident`; `GovernanceAgent.check` | -- | `provenance_entries`, beside `session_state` / `audit_log` | The Guardian's record is in the same transaction as its `audit_log` row |
| `GET /api/v1/provenance/*`, `POST .../session/{id}/erasure` | `provenance_entries` not hidden by a tombstone, and every tombstone | `provenance_erasures` | Governance roles only |
| `POST /api/v1/models`, `/{name}/versions`, `/{name}/reviews` | The model's head | `causal_model_versions` | Append-only; a version is appended only to the head it was built on; proposing needs a drafting role, reviews a governance role |

The DAG Workbench is planned, not built (register `dag-workbench`).
