-- ===========================================================================
-- 007_causal_models.sql
--
-- The assumption registry: causal models as chains of immutable versions, and
-- which decisions rested on which version.
--
--   causal_model_versions  one row per version of a named model. Holds the
--                          causal_decision.assumptions.ModelVersion: the
--                          kernel's CausalGraph (each edge with its origin and
--                          review status), the assumptions a graph cannot
--                          state, who introduced each edge, and the reviews
--                          that produced it. `digest` covers all of it and the
--                          parent link, so a history can be re-verified from
--                          these rows alone.
--   decision_model_refs    one row per decision made against a registered
--                          version, with the query it asked. When the model is
--                          revised, this is what "which verdicts does the
--                          revision affect" is computed over -- refusals
--                          included, since a revision can make an unidentified
--                          question answerable.
--
-- Append-only, like the decision chain they feed: a model version that could be
-- edited after decisions rested on it would make every record naming it a claim
-- about a moving target.
-- ===========================================================================

CREATE TABLE IF NOT EXISTS causal_model_versions (
    digest          TEXT        PRIMARY KEY,
    name            TEXT        NOT NULL,
    version         INTEGER     NOT NULL CHECK (version >= 1),
    parent_digest   TEXT        REFERENCES causal_model_versions(digest),
    author_id       TEXT        NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL,
    record          JSONB       NOT NULL,
    UNIQUE (name, version)
);

CREATE TABLE IF NOT EXISTS decision_model_refs (
    entry_digest    TEXT        PRIMARY KEY,
    model_name      TEXT        NOT NULL,
    model_version   INTEGER     NOT NULL,
    model_digest    TEXT        NOT NULL REFERENCES causal_model_versions(digest),
    verdict         TEXT        NOT NULL,
    action          TEXT        NOT NULL,
    query           JSONB       NOT NULL,  -- adjust_for pinned to the set the analysis used
    unmeasured      JSONB       NOT NULL DEFAULT '[]',  -- model nodes the data had no column for
    recorded_at     TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_decision_model_refs_model
    ON decision_model_refs (model_name, model_version);

REVOKE ALL ON causal_model_versions, decision_model_refs FROM PUBLIC;
GRANT  SELECT, INSERT ON causal_model_versions, decision_model_refs TO causal_app;
GRANT  SELECT, INSERT ON causal_model_versions, decision_model_refs TO causal_governance;
GRANT  SELECT         ON causal_model_versions, decision_model_refs TO causal_readonly;

CREATE OR REPLACE FUNCTION fn_causal_models_no_mutation()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION '% is append-only: % is not permitted', TG_TABLE_NAME, TG_OP
        USING ERRCODE = '42501';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS tg_causal_model_versions_no_update ON causal_model_versions;
CREATE TRIGGER tg_causal_model_versions_no_update
    BEFORE UPDATE OR DELETE ON causal_model_versions
    FOR EACH ROW EXECUTE FUNCTION fn_causal_models_no_mutation();

DROP TRIGGER IF EXISTS tg_decision_model_refs_no_update ON decision_model_refs;
CREATE TRIGGER tg_decision_model_refs_no_update
    BEFORE UPDATE OR DELETE ON decision_model_refs
    FOR EACH ROW EXECUTE FUNCTION fn_causal_models_no_mutation();
