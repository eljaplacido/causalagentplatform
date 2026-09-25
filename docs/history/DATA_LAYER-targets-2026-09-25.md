# DATA_LAYER.md: sections moved out on 2026-09-25

These sections were taken out of [`docs/DATA_LAYER.md`](../DATA_LAYER.md) on 2026-09-25 because
they described targets, not the code. They are kept here word for word as they stood at commit
`13e7d92`, so the design intent is not lost. **Nothing below is implemented or measured.**

- The PostgreSQL table list and DDL named `treatments` and `users` tables that no migration
  creates, and listed evidence, preference and simulation-result storage that does not exist.
  DATA_LAYER.md §3.2 now describes the tables the migrations and the SQLite schema actually create.
- No test or benchmark in this repository ever measured the benchmark targets. The Neo4j and Kafka
  targets stay in DATA_LAYER.md §3.1 and §3.3, inside sections marked as target design.
- None of the business-intelligence connectors was built.

---

## From §3.2 PostgreSQL / Cloud SQL (Relational Store)

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

## From §4.2

### 4.2 Business Intelligence Connectors (Phase 2-3)

| System | Integration Type | Data Pulled |
|---|---|---|
| Salesforce | REST / Bulk API | Customer events, pipeline stages |
| HubSpot | REST API | Campaign events, contact activity |
| Snowflake | Snowpipe / SQL | Aggregated metrics, feature tables |
| BigQuery | REST + Streaming | Event tables, attribution data |
| Looker | Semantic layer embed | Derived metrics, business dimensions |
| dbt | REST / manifest.json | Column lineage, metric definitions |

---

## From §8

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
