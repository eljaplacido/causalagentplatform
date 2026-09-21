-- ===========================================================================
-- 002_verdict_binding.sql
--
-- A Guardian verdict authorises exactly one write.
--
-- Before this migration a verdict was a row id, and `_assert_verdict_approved`
-- checked only that the row existed and said APPROVED. That allowed replay (one
-- APPROVED row authorised unlimited unrelated writes) and forgery (anything that
-- could INSERT into audit_log could mint one -- including the interventions API,
-- which writes APPROVED rows under no role requirement).
--
--   subject_id   the object the verdict was issued for. A verdict is refused for
--                any other object.
--   signature    HMAC-SHA256 over the verdict's claim, keyed by GUARDIAN_SIGNING_KEY.
--                Rows written by anything but the Guardian have none, so they
--                remain a readable audit trail but cannot authorise a write.
--
-- Both columns are nullable: audit_log is append-only and historical rows cannot
-- be backfilled. An unsigned row is refused at use, which is the fail-closed
-- reading -- existing verdicts do not silently keep their old powers.
-- ===========================================================================

ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS subject_id UUID;
ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS signature  TEXT;

COMMENT ON COLUMN audit_log.subject_id IS
    'The object this verdict authorises a write to. NULL means it authorises none.';
COMMENT ON COLUMN audit_log.signature IS
    'HMAC-SHA256 of the verdict claim under the issuer key. NULL means unsigned: '
    'readable as history, refused as authority.';

CREATE INDEX IF NOT EXISTS ix_audit_log_subject_id
    ON audit_log (subject_id)
    WHERE subject_id IS NOT NULL;

-- ===========================================================================
-- TABLE: verdict_consumption
--
-- Single use, recorded where it can be recorded. audit_log is append-only
-- (UPDATE and DELETE are revoked and trapped by triggers), so a verdict cannot
-- be marked spent in place. It is spent by inserting here, and the PRIMARY KEY
-- is what makes a second spend impossible rather than merely discouraged.
-- ===========================================================================
CREATE TABLE IF NOT EXISTS verdict_consumption (
    verdict_id   UUID        PRIMARY KEY REFERENCES audit_log(id),
    subject_id   UUID        NOT NULL,
    context      TEXT        NOT NULL,
    consumed_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE verdict_consumption IS
    'One row per spent Guardian verdict. The primary key enforces single use.';

REVOKE ALL   ON verdict_consumption FROM PUBLIC;
GRANT  SELECT, INSERT ON verdict_consumption TO causal_app;
GRANT  SELECT, INSERT ON verdict_consumption TO causal_governance;
GRANT  SELECT         ON verdict_consumption TO causal_readonly;


-- ===========================================================================
-- causal_entities.guardian_verdict_ref
--
-- Every other governed table records which verdict approved the row;
-- causal_entities never had the column, while promote_to_approved has always
-- issued `SET ... guardian_verdict_ref = :verdict_ref` for it. That UPDATE
-- failed with "no such column" for entities -- unnoticed because /check could
-- never return APPROVED, so the approval path was unreachable end to end.
--
-- Nullable: rows approved before this migration cannot be attributed to a
-- verdict after the fact, and inventing one would be worse than admitting it.
-- ===========================================================================

ALTER TABLE causal_entities
    ADD COLUMN IF NOT EXISTS guardian_verdict_ref UUID REFERENCES audit_log(id);

COMMENT ON COLUMN causal_entities.guardian_verdict_ref IS
    'The Guardian verdict that approved this entity. NULL for rows approved '
    'before the approval path worked end to end.';
