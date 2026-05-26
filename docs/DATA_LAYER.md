# Data Layer, Semantic Layer & Integrations

> **Scope:** Canonical specification for all data storage, causal semantic objects, integration patterns, and event streaming in the Unified Enterprise Causal Platform. All platform components must read and write data through the interfaces defined here.

---

## 1. Architecture Overview

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

1. **All writes require a Guardian `APPROVED` verdict.** No object is persisted without policy clearance.
2. **All objects are versioned.** No in-place mutation. Deprecate and replace.
3. **All approved objects have lineage.** Creator, approver, evidence chain, and Guardian verdict reference are mandatory fields.
4. **Draft objects do not participate in simulations.** Only `APPROVED` graph fragments are accessible to SimulationAgent.
5. **Deprecated objects are retained.** They are filtered from active surfaces but preserved in audit trail.

---

## 3. Storage Architecture

### 3.1 Neo4j (Graph Store)

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

**Benchmark target:** Neo4j 5.x, 3-node cluster for production. d-separation query p95 < 50ms on graphs with ≤ 500 nodes.

---

### 3.2 PostgreSQL / Cloud SQL (Relational Store)

**Used for:**
- Assumption registry (structured metadata)
- Intervention log (actions + outcomes)
- Evidence registry
- Audit trail (append-only partition)
- User session history
- Per-user preferences
- Simulation results

**Key tables:**

```sql
-- Audit trail: append-only, never updated
CREATE TABLE audit_log (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  agent_id    TEXT NOT NULL,
  action_type TEXT NOT NULL,
  payload_hash TEXT NOT NULL,
  guardian_verdict TEXT NOT NULL CHECK (guardian_verdict IN ('APPROVED','REJECTED','REQUIRES_ESCALATION')),
  policy_rules_cited TEXT[],
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- No UPDATE or DELETE permission on this table. Ever.

-- Intervention log with feedback loop
CREATE TABLE interventions (
  id                  UUID PRIMARY KEY,
  treatment_ref       UUID REFERENCES treatments(id),
  initiated_by        UUID REFERENCES users(id),
  approved_by         UUID REFERENCES users(id),
  guardian_verdict_ref UUID REFERENCES audit_log(id),
  expected_effect     JSONB,
  actual_effect       JSONB,
  feedback_loop_closed BOOLEAN DEFAULT false,
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  outcome_observed_at TIMESTAMPTZ
);
```

**Cloud deployment:** Google Cloud SQL (PostgreSQL 16), IAM-authenticated from Cloud Run. Local dev: SQLite with transparent factory switch.

**Benchmark target:** p99 audit_log insert < 5ms, intervention query < 20ms.

---

### 3.3 Kafka (Event Streaming)

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

**Benchmark target:** p95 event latency < 100ms end-to-end from source connector to consumer.

---

### 3.4 SQLite (Local Development)

Transparent factory switch: the `CloudSqlFactory` pattern is the standard. Code never directly instantiates a database connection; it calls the factory which selects SQLite locally and Cloud SQL on Cloud Run (via ADC).

---

## 4. Connector Library

### 4.1 DevOps Connectors (Phase 1 Priority)

| System | Integration Type | Data Pulled | Status |
|---|---|---|---|
| Prometheus | Pull (HTTP scrape) | Metrics time series, alert states | ✅ implemented (`src/connectors/prometheus.py`) |
| Alertmanager | Push webhook | Alert payloads, severity, labels | planned |
| PagerDuty | REST API + webhook | Incidents, escalation events | planned |
| OpsGenie | REST API | On-call schedules, incident data | planned |
| GitHub / GitLab | Webhook + REST | Deployment events, PR metadata | planned |
| Datadog | REST API | APM traces, dashboards | planned |
| Kubernetes | Kubernetes API | Pod events, resource state changes | planned |

### 4.2 Business Intelligence Connectors (Phase 2-3)

| System | Integration Type | Data Pulled |
|---|---|---|
| Salesforce | REST / Bulk API | Customer events, pipeline stages |
| HubSpot | REST API | Campaign events, contact activity |
| Snowflake | Snowpipe / SQL | Aggregated metrics, feature tables |
| BigQuery | REST + Streaming | Event tables, attribution data |
| Looker | Semantic layer embed | Derived metrics, business dimensions |
| dbt | REST / manifest.json | Column lineage, metric definitions |

### 4.3 Connector Interface Contract

All connectors implement the `CausalConnector` interface:

```typescript
interface CausalConnector {
  id: string;
  domain: string;
  version: string;

  // Fetch latest events since cursor
  fetchEvents(cursor: Cursor): Promise<DomainEvent[]>;

  // Subscribe to real-time events via Kafka producer
  subscribe(topic: KafkaTopic): void;

  // Map raw events to semantic layer objects
  mapToSemanticObjects(events: DomainEvent[]): CausalSemanticDraft[];

  // Validate that required data fields are present
  validate(): ConnectorHealthResult;
}
```

All `mapToSemanticObjects` outputs are `DRAFT` status. They go through the CausalDiscoveryAgent and GuardianCheck before promotion to `APPROVED`.

---

## 5. Semantic Drift and Data Quality

### 5.1 Drift Detection Pipeline

```
Live Data Stream → DriftMonitorAgent → KL Divergence Test
                                             │
                         ┌───────────────────┼───────────────────┐
                    < threshold          threshold breach      > severe threshold
                         │                   │                   │
                    No action          Alert emitted         Governance
                                       (Kafka topic)         escalation
                                                           (human review)
```

### 5.2 Drift Types

| Type | Detection Method | Response |
|---|---|---|
| **Causal drift** | Graph structure diverges from live data patterns | Flag graph fragment for re-review |
| **Distribution drift** | KL divergence on feature distributions | Sensitivity re-test on affected simulations |
| **Semantic drift** | Entity definition inconsistency across domains | Semantic layer governance review |
| **Concept drift** | Model performance degradation over time | Re-estimation trigger |

---

## 6. Lineage and Provenance

Every causal asset carries a `lineage` field that traces:
1. Original data source (connector + timestamp)
2. Transformation steps (agent ID, skill used, version)
3. Evidence citations
4. Assumption statements in scope
5. Guardian verdict and policy rules applied
6. Human approver identity and timestamp

This satisfies EU AI Act Article 13 transparency requirements and enables full audit reproduction.

---

## 7. Data Access Patterns by Component

| Component | Read | Write | Notes |
|---|---|---|---|
| CausalDiscoveryAgent | Semantic layer (read), raw events | Draft edges (via audit_write) | Writes only DRAFT objects |
| GovernanceAgent | All semantic layer | Audit trail | Approves/rejects draft objects |
| SimulationAgent | Approved graph fragments | Simulation results | Read-only on semantic layer |
| DriftMonitorAgent | Live data + approved assets | Drift alerts (Kafka) | No semantic layer writes |
| DAG Workbench UI | All semantic objects | Draft edges, assumptions | Through agent intermediaries |
| Decision Cockpit | Approved assets, simulation results | User session state | Read-only on causal layer |
| Vertical Agents | Domain-specific semantic objects | Interventions (governed) | Through Intervention contract |

---

## 8. Reference Benchmarks

| Component | Target | Standard |
|---|---|---|
| Neo4j d-separation query | p95 < 50ms | 500-node graphs |
| Kafka event latency | p95 < 100ms | End-to-end source → consumer |
| PostgreSQL audit insert | p99 < 5ms | Append-only table |
| Simulation estimate (simple) | p95 < 2s | DoWhy linear estimator |
| Simulation estimate (complex) | p95 < 15s | PyMC MCMC 1000 samples |
| Semantic layer object read | p99 < 30ms | PostgreSQL + Neo4j combined |
| cynepic-causal Rust DAG ops | p95 < 1ms | 100-node graph, backdoor criterion |
