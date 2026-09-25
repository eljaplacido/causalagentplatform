-- ===========================================================================
-- 008_session_state.sql
--
-- The durable session store: the latest EpistemicState per session
-- (src/orchestration/session_store.py), and the history the incident triage
-- agent's experience buffer reads back (src/skills/_experience_buffer_sql.py).
--
-- Until this migration the table existed only in SQLITE_DDL
-- (src/db/factory.py), and CloudSqlFactory.init_db runs that DDL only for the
-- local SQLite engine. On PostgreSQL every session snapshot, and so every
-- triage run, failed with "relation session_state does not exist". The suite
-- ran only on SQLite, so nothing said so.
--
-- Mirrors SQLITE_DDL column for column, in this schema's conventions:
-- TIMESTAMPTZ for times, JSONB for the payload. session_id is TEXT, not UUID:
-- session ids come from callers, and nothing requires them to be UUIDs.
--
-- Not append-only: this is the *latest* state, upserted on session_id. The
-- record of what was decided lives in audit_log and decision_records.
-- ===========================================================================

CREATE TABLE IF NOT EXISTS session_state (
    session_id          TEXT        PRIMARY KEY,
    user_id             TEXT,
    agent_id            TEXT        NOT NULL,
    state_json          JSONB       NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE session_state IS
    'Latest EpistemicState snapshot per session; upserted, not append-only.';

-- The experience buffer's query: one agent's most recent sessions.
CREATE INDEX IF NOT EXISTS ix_session_state_agent_updated
    ON session_state (agent_id, updated_at DESC);

REVOKE ALL   ON session_state FROM PUBLIC;
GRANT  SELECT, INSERT, UPDATE ON session_state TO causal_app;
GRANT  SELECT, INSERT, UPDATE ON session_state TO causal_governance;
GRANT  SELECT                 ON session_state TO causal_readonly;
