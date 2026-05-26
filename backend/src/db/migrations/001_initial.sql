-- =============================================================================
-- Migration 001: Initial schema for the Unified Enterprise Causal Platform
--
-- Targets: PostgreSQL 16 / Google Cloud SQL PostgreSQL 16
--
-- Conventions:
--   * All PKs are UUID (gen_random_uuid())
--   * All timestamps are TIMESTAMPTZ
--   * JSONB for schemaless / evolving payload columns
--   * TEXT[] for tag and rule-citation arrays
--   * Row-Level Security (RLS) policies restrict data access by domain
--   * audit_log is append-only: revoke UPDATE/DELETE after creation
--
-- Run with a superuser to create roles, then grant appropriately.
-- =============================================================================

BEGIN;

-- ---------------------------------------------------------------------------
-- Extensions
-- ---------------------------------------------------------------------------
CREATE EXTENSION IF NOT EXISTS "pgcrypto";   -- gen_random_uuid()
CREATE EXTENSION IF NOT EXISTS "pg_trgm";    -- trigram index for text search


-- ---------------------------------------------------------------------------
-- Roles (idempotent)
-- ---------------------------------------------------------------------------
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'causal_app') THEN
        CREATE ROLE causal_app LOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'causal_governance') THEN
        CREATE ROLE causal_governance LOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'causal_readonly') THEN
        CREATE ROLE causal_readonly LOGIN;
    END IF;
END
$$;


-- ===========================================================================
-- TABLE: audit_log
--
-- Append-only, immutable record of every Guardian verdict and agent action.
-- EU AI Act Art. 13, GDPR Art. 30 compliance anchor.
--
-- Invariant: no UPDATE or DELETE is ever permitted (enforced at role level
-- below AND via revoke statements). This is the single most critical table
-- in the platform.
-- ===========================================================================
CREATE TABLE IF NOT EXISTS audit_log (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id            TEXT        NOT NULL,
    action_type         TEXT        NOT NULL,
    payload_hash        TEXT        NOT NULL,
    guardian_verdict    TEXT        NOT NULL
        CHECK (guardian_verdict IN ('APPROVED', 'REJECTED', 'REQUIRES_ESCALATION')),
    policy_rules_cited  TEXT[]      NOT NULL DEFAULT '{}',
    session_id          TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE audit_log IS
    'Append-only Guardian verdict and agent action log. '
    'UPDATE and DELETE are REVOKED from all roles. EU AI Act Art. 13 anchor.';

-- Immutability: grant only INSERT + SELECT.  No UPDATE.  No DELETE.
REVOKE ALL   ON audit_log FROM PUBLIC;
GRANT  SELECT, INSERT ON audit_log TO causal_app;
GRANT  SELECT, INSERT ON audit_log TO causal_governance;
GRANT  SELECT         ON audit_log TO causal_readonly;

-- Performance index (most common query: lookup by agent_id + verdict + time range)
CREATE INDEX IF NOT EXISTS ix_audit_log_agent_id
    ON audit_log (agent_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_audit_log_session_id
    ON audit_log (session_id)
    WHERE session_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_audit_log_verdict
    ON audit_log (guardian_verdict, created_at DESC);


-- ===========================================================================
-- TABLE: causal_entities
--
-- Governed registry of business-meaningful causal units (SERVICE, CUSTOMER,
-- SUPPLIER, CAMPAIGN, ACCOUNT, INCIDENT, DEPLOYMENT, FEATURE, EMPLOYEE, etc.)
-- ===========================================================================
CREATE TABLE IF NOT EXISTS causal_entities (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    name                TEXT        NOT NULL,
    entity_type         TEXT        NOT NULL
        CHECK (entity_type IN (
            'SERVICE','CUSTOMER','SUPPLIER','CAMPAIGN','ACCOUNT',
            'INCIDENT','DEPLOYMENT','FEATURE','EMPLOYEE','OTHER'
        )),
    domain              TEXT        NOT NULL,
    description         TEXT,
    attributes          JSONB       NOT NULL DEFAULT '{}',
    ontology_version    TEXT        NOT NULL DEFAULT '1.0.0',
    approval_status     TEXT        NOT NULL DEFAULT 'DRAFT'
        CHECK (approval_status IN ('DRAFT','UNDER_REVIEW','APPROVED','DEPRECATED')),
    created_by          JSONB       NOT NULL,   -- UserRef { id, email, name }
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    approved_at         TIMESTAMPTZ,
    approved_by         JSONB,                  -- UserRef | null
    lineage             JSONB       NOT NULL DEFAULT '[]',
    tags                TEXT[]      NOT NULL DEFAULT '{}'
);

COMMENT ON TABLE causal_entities IS
    'Governed registry of business-meaningful causal entities. '
    'All writes require an APPROVED Guardian verdict.';

GRANT SELECT, INSERT, UPDATE ON causal_entities TO causal_app;
GRANT SELECT, INSERT, UPDATE ON causal_entities TO causal_governance;
GRANT SELECT ON causal_entities TO causal_readonly;

CREATE INDEX IF NOT EXISTS ix_causal_entities_domain_status
    ON causal_entities (domain, approval_status);
CREATE INDEX IF NOT EXISTS ix_causal_entities_tags
    ON causal_entities USING GIN (tags);

-- Row-level security: users can only read entities in their granted domains.
ALTER TABLE causal_entities ENABLE ROW LEVEL SECURITY;

CREATE POLICY causal_entities_domain_isolation
    ON causal_entities
    FOR ALL
    USING (
        -- current_setting('app.user_domains') is set by the application layer
        -- to a comma-separated list of domains the authenticated user may access.
        domain = ANY(
            string_to_array(
                current_setting('app.user_domains', true),
                ','
            )
        )
        OR current_setting('app.user_role', true) IN (
            'governance_officer', 'platform_engineer'
        )
    );


-- ===========================================================================
-- TABLE: assumptions
--
-- Explicit causal assumptions that cannot be derived from data alone.
-- IGNORABILITY | POSITIVITY | SUTVA | EXCLUSION_RESTRICTION | STRUCTURAL | DOMAIN
-- ===========================================================================
CREATE TABLE IF NOT EXISTS assumptions (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    statement           TEXT        NOT NULL,
    assumption_type     TEXT        NOT NULL
        CHECK (assumption_type IN (
            'IGNORABILITY','POSITIVITY','SUTVA',
            'EXCLUSION_RESTRICTION','STRUCTURAL','DOMAIN'
        )),
    scope               JSONB       NOT NULL DEFAULT '[]',  -- CausalObjectRef[]
    justification       TEXT        NOT NULL,
    elicited_from       TEXT        NOT NULL
        CHECK (elicited_from IN (
            'DOMAIN_EXPERT','AGENT','LITERATURE','PRIOR_STUDY'
        )),
    elicited_by         JSONB       NOT NULL,   -- UserRef
    elicited_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    review_status       TEXT        NOT NULL DEFAULT 'PENDING'
        CHECK (review_status IN ('PENDING','ACCEPTED','CONTESTED','REJECTED')),
    last_reviewed_at    TIMESTAMPTZ,
    next_review_due     TIMESTAMPTZ,
    sensitivity_result  JSONB,                  -- SensitivityResult | null
    domain              TEXT        NOT NULL
);

COMMENT ON TABLE assumptions IS
    'Explicit causal assumptions. Requires sensitivity analysis before ACCEPTED.';

GRANT SELECT, INSERT, UPDATE ON assumptions TO causal_app;
GRANT SELECT, INSERT, UPDATE ON assumptions TO causal_governance;
GRANT SELECT ON assumptions TO causal_readonly;

CREATE INDEX IF NOT EXISTS ix_assumptions_domain_status
    ON assumptions (domain, review_status);
CREATE INDEX IF NOT EXISTS ix_assumptions_next_review
    ON assumptions (next_review_due)
    WHERE next_review_due IS NOT NULL AND review_status = 'PENDING';


-- ===========================================================================
-- TABLE: causal_edges
--
-- Directed, typed, evidenced causal relationships.
--
-- Key invariant: every edge MUST reference a valid APPROVED Guardian verdict
-- in audit_log via guardian_verdict_ref.  The FK + CHECK constraint enforces
-- this at the database level — no code-level bypass is possible.
-- ===========================================================================
CREATE TABLE IF NOT EXISTS causal_edges (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id           UUID        NOT NULL,
    source_type         TEXT        NOT NULL,
    target_id           UUID        NOT NULL,
    target_type         TEXT        NOT NULL,
    edge_type           TEXT        NOT NULL
        CHECK (edge_type IN ('CAUSES','MEDIATES','CONFOUNDS','MODERATES','BLOCKS')),
    direction           TEXT        NOT NULL DEFAULT 'FORWARD'
        CHECK (direction IN ('FORWARD','BACKWARD','BIDIRECTIONAL_FORBIDDEN')),
    confidence          NUMERIC(4,3) NOT NULL
        CHECK (confidence >= 0.0 AND confidence <= 1.0),
    evidence_refs       UUID[]      NOT NULL DEFAULT '{}',
    assumption_refs     UUID[]      NOT NULL DEFAULT '{}',
    approval_status     TEXT        NOT NULL DEFAULT 'DRAFT'
        CHECK (approval_status IN ('DRAFT','UNDER_REVIEW','APPROVED','DEPRECATED')),
    approved_by         JSONB,                  -- UserRef | null
    approved_at         TIMESTAMPTZ,
    -- Guardian FK: this column CANNOT be NULL for APPROVED edges.
    -- The application-level GuardianBlockedError fires before we even reach
    -- the DB, but the FK provides a second defence-in-depth layer.
    guardian_verdict_ref UUID       NOT NULL
        REFERENCES audit_log(id)
        DEFERRABLE INITIALLY DEFERRED,
    version             TEXT        NOT NULL DEFAULT '1.0.0',
    lineage             JSONB       NOT NULL DEFAULT '[]',
    domain              TEXT        NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE causal_edges IS
    'Directed causal edges. Every edge requires a valid Guardian APPROVED verdict '
    'referenced via guardian_verdict_ref (FK to audit_log).';

GRANT SELECT, INSERT, UPDATE ON causal_edges TO causal_app;
GRANT SELECT, INSERT, UPDATE ON causal_edges TO causal_governance;
GRANT SELECT ON causal_edges TO causal_readonly;

CREATE INDEX IF NOT EXISTS ix_causal_edges_source
    ON causal_edges (source_id, source_type);
CREATE INDEX IF NOT EXISTS ix_causal_edges_target
    ON causal_edges (target_id, target_type);
CREATE INDEX IF NOT EXISTS ix_causal_edges_domain_status
    ON causal_edges (domain, approval_status);
CREATE INDEX IF NOT EXISTS ix_causal_edges_verdict
    ON causal_edges (guardian_verdict_ref);

-- RLS: domain isolation matching causal_entities policy
ALTER TABLE causal_edges ENABLE ROW LEVEL SECURITY;

CREATE POLICY causal_edges_domain_isolation
    ON causal_edges
    FOR ALL
    USING (
        domain = ANY(
            string_to_array(
                current_setting('app.user_domains', true),
                ','
            )
        )
        OR current_setting('app.user_role', true) IN (
            'governance_officer', 'platform_engineer'
        )
    );


-- ===========================================================================
-- TABLE: graph_fragments
--
-- Named, versioned, reusable sub-DAGs.  Only APPROVED fragments may be used
-- in SimulationAgent.
-- ===========================================================================
CREATE TABLE IF NOT EXISTS graph_fragments (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    name                TEXT        NOT NULL,
    domain              TEXT        NOT NULL,
    description         TEXT        NOT NULL DEFAULT '',
    edge_ids            UUID[]      NOT NULL DEFAULT '{}',  -- CausalEdge IDs
    entry_nodes         JSONB       NOT NULL DEFAULT '[]',  -- CausalObjectRef[]
    exit_nodes          JSONB       NOT NULL DEFAULT '[]',  -- CausalObjectRef[]
    approval_status     TEXT        NOT NULL DEFAULT 'DRAFT'
        CHECK (approval_status IN ('DRAFT','APPROVED','DEPRECATED')),
    version             TEXT        NOT NULL DEFAULT '1.0.0',
    usage_count         INTEGER     NOT NULL DEFAULT 0,
    tags                TEXT[]      NOT NULL DEFAULT '{}',
    guardian_verdict_ref UUID
        REFERENCES audit_log(id)
        DEFERRABLE INITIALLY DEFERRED,
    created_by          JSONB       NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    approved_at         TIMESTAMPTZ,
    approved_by         JSONB
);

COMMENT ON TABLE graph_fragments IS
    'Named, versioned sub-DAGs. Only APPROVED fragments are accessible to SimulationAgent.';

GRANT SELECT, INSERT, UPDATE ON graph_fragments TO causal_app;
GRANT SELECT, INSERT, UPDATE ON graph_fragments TO causal_governance;
GRANT SELECT ON graph_fragments TO causal_readonly;

CREATE UNIQUE INDEX IF NOT EXISTS ux_graph_fragments_name_version_domain
    ON graph_fragments (name, version, domain);
CREATE INDEX IF NOT EXISTS ix_graph_fragments_domain_status
    ON graph_fragments (domain, approval_status);
CREATE INDEX IF NOT EXISTS ix_graph_fragments_tags
    ON graph_fragments USING GIN (tags);


-- ===========================================================================
-- TABLE: interventions
--
-- Governed log of real-world actions taken with causal intent.
-- Tracks expected vs actual effects and feedback loop closure.
-- ===========================================================================
CREATE TABLE IF NOT EXISTS interventions (
    id                      UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    name                    TEXT        NOT NULL,
    treatment_ref           UUID,       -- references treatments concept; loose FK
    target_entity_ref       UUID,       -- references causal_entities(id) conceptually
    initiated_by            JSONB       NOT NULL,   -- UserRef
    initiated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    approved_by             JSONB       NOT NULL,   -- UserRef
    approval_timestamp      TIMESTAMPTZ NOT NULL,
    guardian_verdict_ref    UUID        NOT NULL
        REFERENCES audit_log(id)
        DEFERRABLE INITIALLY DEFERRED,
    expected_effect         JSONB       NOT NULL,   -- EffectEstimate
    actual_effect           JSONB,                  -- EffectMeasurement | null
    feedback_loop_closed    BOOLEAN     NOT NULL DEFAULT false,
    outcome_observed_at     TIMESTAMPTZ,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE interventions IS
    'Governed intervention log with before/after effect tracking and feedback closure.';

GRANT SELECT, INSERT, UPDATE ON interventions TO causal_app;
GRANT SELECT, INSERT, UPDATE ON interventions TO causal_governance;
GRANT SELECT ON interventions TO causal_readonly;

CREATE INDEX IF NOT EXISTS ix_interventions_verdict
    ON interventions (guardian_verdict_ref);
CREATE INDEX IF NOT EXISTS ix_interventions_feedback_open
    ON interventions (feedback_loop_closed, outcome_observed_at)
    WHERE feedback_loop_closed = false;


-- ===========================================================================
-- Triggers: enforce audit_log immutability at the trigger level
-- (Belt-and-suspenders on top of REVOKE)
-- ===========================================================================
CREATE OR REPLACE FUNCTION fn_audit_log_no_mutation()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION
        'audit_log is append-only: % operations are forbidden. '
        'Verdict id: %',
        TG_OP,
        OLD.id
    USING ERRCODE = '42501';  -- insufficient_privilege
END;
$$;

DROP TRIGGER IF EXISTS tg_audit_log_no_update ON audit_log;
CREATE TRIGGER tg_audit_log_no_update
    BEFORE UPDATE ON audit_log
    FOR EACH ROW
    EXECUTE FUNCTION fn_audit_log_no_mutation();

DROP TRIGGER IF EXISTS tg_audit_log_no_delete ON audit_log;
CREATE TRIGGER tg_audit_log_no_delete
    BEFORE DELETE ON audit_log
    FOR EACH ROW
    EXECUTE FUNCTION fn_audit_log_no_mutation();


-- ===========================================================================
-- Trigger: auto-increment graph_fragments.usage_count when a fragment is
-- referenced in a simulation (application updates via this trigger path)
-- ===========================================================================
CREATE OR REPLACE FUNCTION fn_increment_fragment_usage()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    UPDATE graph_fragments
    SET usage_count = usage_count + 1
    WHERE id = NEW.fragment_id;
    RETURN NEW;
END;
$$;


-- ===========================================================================
-- Grant sequence usage
-- ===========================================================================
GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO causal_app;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO causal_governance;

COMMIT;
