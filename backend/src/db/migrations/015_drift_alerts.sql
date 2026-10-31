-- ===========================================================================
-- 015_drift_alerts.sql
--
-- A record of every drift alert DriftMonitorAgent raises (septa-62).
--
-- Drift alerts were stored only as (:DriftAlert) nodes in Neo4j, and only when
-- NEO4J_URI was set; without it an alert was logged by the in-process
-- AlertEmitter and kept nowhere, so a deployment on PostgreSQL alone (the
-- supported one) had no trail of the alerts it raised. This table is the SQL
-- fallback: DriftMonitorAgent writes a row for every alert Neo4j did not store
-- (Neo4j not configured, storage disabled, or the write failed), so every
-- alert leaves a record. src/sensors/drift_alert_log.py writes and reads it.
--
-- Mirrors SQLITE_DDL column for column: TIMESTAMPTZ for the time, JSONB for
-- the feature list and the context, DOUBLE PRECISION for the score. id is a
-- uuid4 as TEXT, minted for the row; the AlertEmitter's own alert id, when the
-- alert was emitted, is in context.emitted_alert_id.
--
-- Append-only, by grant: a record of an alert is not edited afterwards. There
-- is no acknowledgement column; acknowledging is a Neo4j-only feature.
-- ===========================================================================

CREATE TABLE IF NOT EXISTS drift_alerts (
    id                 TEXT             PRIMARY KEY,
    alert_type         TEXT             NOT NULL,
    severity           TEXT             NOT NULL
        CHECK (severity IN ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL')),
    drift_type         TEXT             NOT NULL,
    fragment_id        TEXT,
    drift_score        DOUBLE PRECISION NOT NULL,
    drifted_features   JSONB            NOT NULL DEFAULT '[]',
    description        TEXT             NOT NULL,
    recommendation     TEXT,
    context            JSONB            NOT NULL DEFAULT '{}',
    session_id         TEXT,
    agent_id           TEXT             NOT NULL,
    created_at         TIMESTAMPTZ      NOT NULL DEFAULT now()
);

COMMENT ON TABLE drift_alerts IS
    'Every drift alert DriftMonitorAgent raised that Neo4j did not store. '
    'Append-only by grant.';

CREATE INDEX IF NOT EXISTS ix_drift_alerts_created_at ON drift_alerts (created_at DESC);
CREATE INDEX IF NOT EXISTS ix_drift_alerts_fragment   ON drift_alerts (fragment_id, created_at DESC);

REVOKE ALL   ON drift_alerts FROM PUBLIC;
GRANT  SELECT, INSERT ON drift_alerts TO causal_app;
GRANT  SELECT, INSERT ON drift_alerts TO causal_governance;
GRANT  SELECT         ON drift_alerts TO causal_readonly;
