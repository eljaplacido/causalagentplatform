-- ===========================================================================
-- 015_decision_shadow.sql
--
-- Shadow decisions are listed apart from real ones (septa-95).
--
-- A shadow decision (shadow/README.md) is analysed, ruled on and sealed like
-- any other, but nobody acts on it and it is never emitted to the second brain.
-- Its tag is sealed inside `record` (DecisionRecord.shadow), so it is part of
-- the entry digest: a shadow verdict cannot be relabelled as real, or a real
-- one filed away as shadow, without breaking the chain.
--
-- This column is an index over that field, like `subject` and `verdict`: it
-- lets the API list real and shadow decisions apart without parsing every row.
-- It is not a source of truth; verification reads `record`. NULL for a real
-- decision, and for every row written before this migration (all of them
-- real: the field did not exist).
-- ===========================================================================

ALTER TABLE decision_records
    ADD COLUMN IF NOT EXISTS shadow TEXT;

COMMENT ON COLUMN decision_records.shadow IS
    'The shadow tag sealed in `record`, NULL for a real decision. An index over '
    'the record, not a source of truth.';

CREATE INDEX IF NOT EXISTS ix_decision_records_shadow
    ON decision_records (shadow, sequence DESC);
