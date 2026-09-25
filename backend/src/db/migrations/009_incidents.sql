-- ===========================================================================
-- 009_incidents.sql
--
-- Incidents submitted for triage (src/api/incidents.py): what was reported,
-- what the pipeline concluded, and the interventions it proposed. The human
-- decisions on those interventions are not stored here; they are rows in
-- audit_log, joined in on read.
--
-- Like session_state (008), this table existed only in SQLITE_DDL, so on
-- PostgreSQL the incidents API failed with a missing relation -- and its write
-- was INSERT OR REPLACE, which PostgreSQL does not accept either. The write is
-- now INSERT ... ON CONFLICT (id) DO UPDATE, which both dialects run.
--
-- Mirrors SQLITE_DDL column for column: TIMESTAMPTZ for times, JSONB for the
-- lists, DOUBLE PRECISION for the confidences. id is TEXT: incident ids come
-- from upstream systems (PagerDuty and the like), not from this platform.
--
-- Not append-only: a resubmitted incident updates its row (created_at is kept).
-- ===========================================================================

CREATE TABLE IF NOT EXISTS incidents (
    id                       TEXT        PRIMARY KEY,
    title                    TEXT        NOT NULL,
    severity                 TEXT        NOT NULL,
    affected_services        JSONB       NOT NULL DEFAULT '[]',
    status                   TEXT        NOT NULL DEFAULT 'ACTIVE',
    session_id               TEXT,
    cynefin_domain           TEXT,
    domain_confidence        DOUBLE PRECISION,
    overall_confidence       DOUBLE PRECISION,
    guardian_verdict         TEXT,
    draft_explanation        TEXT        NOT NULL DEFAULT '',
    causal_hypotheses        JSONB       NOT NULL DEFAULT '[]',
    proposed_interventions   JSONB       NOT NULL DEFAULT '[]',
    submitted_by             TEXT        NOT NULL,
    created_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at               TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE incidents IS
    'Incidents submitted for triage and what the pipeline concluded. Decisions on '
    'the proposed interventions are audit_log rows, joined in on read.';

CREATE INDEX IF NOT EXISTS ix_incidents_created_at ON incidents (created_at DESC);
CREATE INDEX IF NOT EXISTS ix_incidents_status     ON incidents (status);

REVOKE ALL   ON incidents FROM PUBLIC;
GRANT  SELECT, INSERT, UPDATE ON incidents TO causal_app;
GRANT  SELECT, INSERT, UPDATE ON incidents TO causal_governance;
GRANT  SELECT                 ON incidents TO causal_readonly;
