-- ===========================================================================
-- 003_decision_records.sql
--
-- The decision ledger: one row per link in the hash chain that
-- causal_decision.ledger builds. Each row holds the sealed record as JSON,
-- plus the few fields needed to walk and serve the chain without parsing
-- every row.
--
-- Those columns are a *copy* of what the record already says. The verifier
-- reads `record` and recomputes the digests, so a row whose columns disagree
-- with its own record does not verify -- which is the case worth catching,
-- since altering the cheap columns is easier than altering the JSON.
--
-- Append-only, like audit_log and for the same reason: a record of what was
-- decided is worth nothing if the decider can revise it afterwards. The chain
-- makes tampering detectable; these grants and triggers make it awkward.
-- Neither makes it impossible -- that needs the head digest anchored somewhere
-- this database's owner does not control.
-- ===========================================================================

CREATE TABLE IF NOT EXISTS decision_records (
    sequence         BIGINT      PRIMARY KEY,
    entry_digest     TEXT        NOT NULL UNIQUE,
    prev_digest      TEXT        NOT NULL,
    decision_digest  TEXT        NOT NULL,
    subject          TEXT        NOT NULL,
    verdict          TEXT        NOT NULL
        CHECK (verdict IN ('act', 'act_with_approval', 'experiment', 'refuse')),
    recorded_at      TIMESTAMPTZ NOT NULL,
    record           JSONB       NOT NULL
);

COMMENT ON TABLE decision_records IS
    'Append-only hash chain of causal decisions. Verified by recomputing from '
    '`record`; the other columns are an index over it, not a source of truth.';

CREATE INDEX IF NOT EXISTS ix_decision_records_subject
    ON decision_records (subject, sequence DESC);
CREATE INDEX IF NOT EXISTS ix_decision_records_verdict
    ON decision_records (verdict, recorded_at DESC);

REVOKE ALL   ON decision_records FROM PUBLIC;
GRANT  SELECT, INSERT ON decision_records TO causal_app;
GRANT  SELECT, INSERT ON decision_records TO causal_governance;
GRANT  SELECT         ON decision_records TO causal_readonly;

CREATE OR REPLACE FUNCTION fn_decision_records_no_mutation()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'decision_records is append-only: % is not permitted', TG_OP
        USING ERRCODE = '42501';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS tg_decision_records_no_update ON decision_records;
CREATE TRIGGER tg_decision_records_no_update
    BEFORE UPDATE ON decision_records
    FOR EACH ROW EXECUTE FUNCTION fn_decision_records_no_mutation();

DROP TRIGGER IF EXISTS tg_decision_records_no_delete ON decision_records;
CREATE TRIGGER tg_decision_records_no_delete
    BEFORE DELETE ON decision_records
    FOR EACH ROW EXECUTE FUNCTION fn_decision_records_no_mutation();
