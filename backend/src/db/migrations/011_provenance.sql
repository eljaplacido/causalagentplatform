-- ===========================================================================
-- 011_provenance.sql
--
-- Durable, append-only provenance (ADR-002 Part A;
-- src/semantic_layer/provenance_store.py).
--
-- Until this migration the provenance API kept its records in two module-level
-- dicts: lost on restart, and erased by DELETE /api/v1/provenance/session/{id}
-- (register entries provenance-not-append-only, provenance-store-no-callers).
--
-- provenance_entries  one row per PROV record or LLM invocation, the model as
--                     JSON in `body`. `sequence` orders a session's entries and
--                     is what an erasure's watermark refers to.
-- provenance_erasures one row per erasure: which session, who, when, why, and
--                     the sequence up to which that session's entries are hidden
--                     from the API. An erasure is a tombstone, not a deletion.
--
-- What an erasure does and does not do. The API stops returning the hidden
-- entries, and returns the tombstone in their place, so the fact that something
-- was erased, by whom and why, stays readable. The hidden rows are NOT removed:
-- both tables are append-only, so the content remains in this database, readable
-- by its owner and by any role granted SELECT. That is not erasure in the sense
-- of GDPR Art. 17 (register gdpr-controls). causal_readonly is deliberately not
-- granted SELECT on provenance_entries, so the hidden content is not readable
-- through that role either.
--
-- Append-only, like audit_log (001) and decision_records (003), for the same
-- reason: a provenance record the recorder can revise afterwards proves
-- nothing. causal_app gets SELECT and INSERT; UPDATE and DELETE are granted to
-- no role and trapped by a trigger for the roles grants do not bind (the owner).
-- Neither table holds a domain, so neither is under a row-level policy; the
-- application restricts every provenance route to governance roles
-- (src/middleware/rbac.py).
-- ===========================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS provenance_entries (
    sequence        BIGSERIAL   PRIMARY KEY,
    id              UUID        NOT NULL UNIQUE,
    kind            TEXT        NOT NULL
        CHECK (kind IN ('record', 'invocation')),
    session_id      TEXT        NOT NULL,
    agent_id        TEXT        NOT NULL,
    recorded_at     TIMESTAMPTZ NOT NULL,
    body            JSONB       NOT NULL
);

COMMENT ON TABLE provenance_entries IS
    'Append-only PROV records and LLM invocations, per session. Hidden from the '
    'API, never removed, by a provenance_erasures tombstone.';

CREATE INDEX IF NOT EXISTS ix_provenance_entries_session
    ON provenance_entries (session_id, sequence);

CREATE TABLE IF NOT EXISTS provenance_erasures (
    id              UUID        PRIMARY KEY,
    session_id      TEXT        NOT NULL,
    hides_through   BIGINT      NOT NULL CHECK (hides_through >= 0),
    hidden_count    INTEGER     NOT NULL CHECK (hidden_count >= 0),
    erased_by       TEXT        NOT NULL CHECK (length(btrim(erased_by)) > 0),
    reason          TEXT        NOT NULL CHECK (length(btrim(reason)) >= 10),
    erased_at       TIMESTAMPTZ NOT NULL
);

COMMENT ON TABLE provenance_erasures IS
    'Tombstones: a session''s provenance entries up to hides_through are hidden '
    'from the API. Who, when and why are kept; the entries themselves are not deleted.';

CREATE INDEX IF NOT EXISTS ix_provenance_erasures_session
    ON provenance_erasures (session_id, hides_through);

REVOKE ALL   ON provenance_entries  FROM PUBLIC;
REVOKE ALL   ON provenance_erasures FROM PUBLIC;
GRANT  SELECT, INSERT ON provenance_entries  TO causal_app;
GRANT  SELECT, INSERT ON provenance_entries  TO causal_governance;
GRANT  SELECT, INSERT ON provenance_erasures TO causal_app;
GRANT  SELECT, INSERT ON provenance_erasures TO causal_governance;
GRANT  SELECT         ON provenance_erasures TO causal_readonly;

-- provenance_entries.sequence is new; 010 granted USAGE only on the sequences
-- that existed then.
GRANT USAGE ON SEQUENCE provenance_entries_sequence_seq TO causal_app;
GRANT USAGE ON SEQUENCE provenance_entries_sequence_seq TO causal_governance;

CREATE OR REPLACE FUNCTION fn_provenance_no_mutation()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION '% is append-only: % is not permitted', TG_TABLE_NAME, TG_OP
        USING ERRCODE = '42501';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS tg_provenance_entries_no_update ON provenance_entries;
CREATE TRIGGER tg_provenance_entries_no_update
    BEFORE UPDATE ON provenance_entries
    FOR EACH ROW EXECUTE FUNCTION fn_provenance_no_mutation();

DROP TRIGGER IF EXISTS tg_provenance_entries_no_delete ON provenance_entries;
CREATE TRIGGER tg_provenance_entries_no_delete
    BEFORE DELETE ON provenance_entries
    FOR EACH ROW EXECUTE FUNCTION fn_provenance_no_mutation();

DROP TRIGGER IF EXISTS tg_provenance_erasures_no_update ON provenance_erasures;
CREATE TRIGGER tg_provenance_erasures_no_update
    BEFORE UPDATE ON provenance_erasures
    FOR EACH ROW EXECUTE FUNCTION fn_provenance_no_mutation();

DROP TRIGGER IF EXISTS tg_provenance_erasures_no_delete ON provenance_erasures;
CREATE TRIGGER tg_provenance_erasures_no_delete
    BEFORE DELETE ON provenance_erasures
    FOR EACH ROW EXECUTE FUNCTION fn_provenance_no_mutation();

COMMIT;
