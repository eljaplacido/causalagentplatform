-- ===========================================================================
-- 004_decision_followups.sql
--
-- What happened after a decision. One row per event, never updated:
--
--   kind='plan'         written when the decision is made. Holds the
--                       causal_decision.outcome.FollowUp: the graph and query
--                       to re-run, the window, and -- copied, not referenced --
--                       the effect and interval the verdict actually used.
--   kind='realization'  written when the follow-up runs. Holds the
--                       causal_decision.outcome.Realization: the later
--                       estimate set beside that prediction, or the kernel's
--                       typed reason why the later data could not answer.
--
-- The prediction is copied into the plan on purpose. A follow-up is only
-- evidence about a verdict if the number it is scored against is the number
-- that verdict used, and a later re-analysis of the original window would give
-- a slightly different one. Scoring against that instead would let the platform
-- move the target after seeing the result.
--
-- An UNDECIDABLE realization is a row like any other. A calibration report that
-- dropped them would be scored on the decisions whose effect happened to stay
-- measurable -- and an action rolled out to everybody is exactly the one that
-- stops being measurable, so they are not missing at random.
--
-- Append-only, like audit_log and decision_records, for the same reason.
-- ===========================================================================

CREATE TABLE IF NOT EXISTS decision_followups (
    id               BIGSERIAL   PRIMARY KEY,
    entry_digest     TEXT        NOT NULL,
    decision_digest  TEXT        NOT NULL,
    kind             TEXT        NOT NULL
        CHECK (kind IN ('plan', 'realization')),
    decidable        BOOLEAN,
    created_at       TIMESTAMPTZ NOT NULL,
    payload          JSONB       NOT NULL
);

COMMENT ON TABLE decision_followups IS
    'Plans to re-measure a decision, and the measurements. Append-only; '
    'undecidable measurements are kept, because they are not missing at random.';

CREATE INDEX IF NOT EXISTS ix_decision_followups_entry
    ON decision_followups (entry_digest, id);
CREATE INDEX IF NOT EXISTS ix_decision_followups_kind
    ON decision_followups (kind, created_at DESC);

REVOKE ALL   ON decision_followups FROM PUBLIC;
GRANT  SELECT, INSERT ON decision_followups TO causal_app;
GRANT  SELECT, INSERT ON decision_followups TO causal_governance;
GRANT  SELECT         ON decision_followups TO causal_readonly;

CREATE OR REPLACE FUNCTION fn_decision_followups_no_mutation()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'decision_followups is append-only: % is not permitted', TG_OP
        USING ERRCODE = '42501';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS tg_decision_followups_no_update ON decision_followups;
CREATE TRIGGER tg_decision_followups_no_update
    BEFORE UPDATE ON decision_followups
    FOR EACH ROW EXECUTE FUNCTION fn_decision_followups_no_mutation();

DROP TRIGGER IF EXISTS tg_decision_followups_no_delete ON decision_followups;
CREATE TRIGGER tg_decision_followups_no_delete
    BEFORE DELETE ON decision_followups
    FOR EACH ROW EXECUTE FUNCTION fn_decision_followups_no_mutation();
