-- ===========================================================================
-- 014_semantic_object_versions.sql
--
-- Semantic objects are versioned: a state change writes a new version, and no
-- version is ever written over (ADR-001, "All objects are versioned -- no
-- in-place mutation; deprecate and replace"; septa-14).
--
-- Numbered 014: 011, 012 and 013 are taken by open branches (011_provenance,
-- 012_edge_created_by, 013_assumption_sensitivity_verdict). Nothing here
-- depends on any of them.
--
-- Before this, promote_to_approved and reject_draft UPDATEd the object's own
-- row -- status, approver, verdict reference, lineage -- and nothing kept what
-- the row had said. The draft verdict an edge was written under was replaced
-- by the approval verdict, so even the audit trail's link to it was lost.
--
--   semantic_object_versions  one row per revision of a CausalEntity,
--                             CausalEdge, GraphFragment or Assumption: the
--                             object's complete row as it stood at that
--                             revision (`state`), what changed it, who (the
--                             person and the agent it came through, with the
--                             platform version), and the verdict that
--                             authorised it. Append-only.
--   <table>.revision          the revision the head row holds. The row in the
--                             object's own table is the head -- the current
--                             version -- and a state change advances it by
--                             exactly one, after recording the state it
--                             replaces.
--   <table>.source_data       the data the object was drafted from: a reference
--                             and a sha256 digest (septa-15). Approval requires
--                             one.
--
-- What the database holds, whoever connects:
--   * version rows cannot be updated or deleted (triggers, and causal_app has
--     only SELECT and INSERT);
--   * a head row may change only by advancing `revision` by one, and only when
--     the state it replaces is already recorded (BEFORE UPDATE);
--   * the new revision must be recorded by the end of the transaction
--     (a deferred constraint trigger), so a head cannot move ahead of history.
--
-- Existing rows are migrated forward: each gets revision 1 and a RECORDED
-- version holding the row exactly as this migration found it. The migration is
-- idempotent -- a second run records nothing twice.
--
-- `version` (the semver text on edges and fragments) is untouched: it is the
-- content version an author declares, and part of graph_fragments' unique key.
-- `revision` counts states.
-- ===========================================================================

BEGIN;

-- ---------------------------------------------------------------------------
-- The head tables: revision and source data.
-- ---------------------------------------------------------------------------

ALTER TABLE causal_entities
    ADD COLUMN IF NOT EXISTS revision INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1);
ALTER TABLE causal_edges
    ADD COLUMN IF NOT EXISTS revision INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1);
ALTER TABLE graph_fragments
    ADD COLUMN IF NOT EXISTS revision INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1);
ALTER TABLE assumptions
    ADD COLUMN IF NOT EXISTS revision INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1);

ALTER TABLE causal_entities ADD COLUMN IF NOT EXISTS source_data JSONB;
ALTER TABLE causal_edges    ADD COLUMN IF NOT EXISTS source_data JSONB;
ALTER TABLE graph_fragments ADD COLUMN IF NOT EXISTS source_data JSONB;
ALTER TABLE assumptions     ADD COLUMN IF NOT EXISTS source_data JSONB;

COMMENT ON COLUMN causal_entities.revision IS
    'The revision this head row holds; every revision is a row in semantic_object_versions.';
COMMENT ON COLUMN causal_entities.source_data IS
    'DataSource {kind, ref, digest}: the data the object was drafted from. NULL = not declared.';


-- ---------------------------------------------------------------------------
-- The versions.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS semantic_object_versions (
    object_type     TEXT        NOT NULL
        CHECK (object_type IN ('CausalEntity', 'CausalEdge', 'GraphFragment', 'Assumption')),
    object_id       UUID        NOT NULL,
    revision        INTEGER     NOT NULL CHECK (revision >= 1),
    change          TEXT        NOT NULL
        CHECK (change IN ('PROPOSED', 'APPROVED', 'REJECTED', 'RECORDED')),
    state           JSONB       NOT NULL,
    domain          TEXT        NOT NULL,
    actor           JSONB,                  -- UserRef | null (null when not recorded)
    agent_id        TEXT,
    agent_version   TEXT,
    verdict_ref     UUID        REFERENCES audit_log(id),
    recorded_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (object_type, object_id, revision)
);

COMMENT ON TABLE semantic_object_versions IS
    'Append-only history of semantic objects: each row is an object''s complete state at '
    'one revision. The object''s own table holds the current revision.';

CREATE INDEX IF NOT EXISTS ix_semantic_object_versions_domain
    ON semantic_object_versions (domain, object_type);
CREATE INDEX IF NOT EXISTS ix_semantic_object_versions_verdict
    ON semantic_object_versions (verdict_ref)
    WHERE verdict_ref IS NOT NULL;

REVOKE ALL   ON semantic_object_versions FROM PUBLIC;
GRANT  SELECT, INSERT ON semantic_object_versions TO causal_app;
GRANT  SELECT, INSERT ON semantic_object_versions TO causal_governance;
GRANT  SELECT         ON semantic_object_versions TO causal_readonly;

-- Domain isolation, as for the head tables (010_rls_binds.sql).
DROP POLICY IF EXISTS semantic_object_versions_domain_isolation ON semantic_object_versions;
CREATE POLICY semantic_object_versions_domain_isolation
    ON semantic_object_versions
    FOR ALL
    USING (app_scope_allows(domain))
    WITH CHECK (app_scope_allows(domain));
ALTER TABLE semantic_object_versions ENABLE ROW LEVEL SECURITY;

CREATE OR REPLACE FUNCTION fn_semantic_object_versions_no_mutation()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'semantic_object_versions is append-only: % is not permitted', TG_OP
        USING ERRCODE = '42501';
END;
$$;

DROP TRIGGER IF EXISTS tg_semantic_object_versions_no_mutation ON semantic_object_versions;
CREATE TRIGGER tg_semantic_object_versions_no_mutation
    BEFORE UPDATE OR DELETE ON semantic_object_versions
    FOR EACH ROW EXECUTE FUNCTION fn_semantic_object_versions_no_mutation();


-- ---------------------------------------------------------------------------
-- Migrate existing rows forward: each one's state as found, as revision 1.
-- Runs before the head triggers exist, and touches no head row.
-- ---------------------------------------------------------------------------

INSERT INTO semantic_object_versions
    (object_type, object_id, revision, change, state, domain, agent_id, verdict_ref, recorded_at)
SELECT 'CausalEntity', h.id, h.revision, 'RECORDED', to_jsonb(h), h.domain,
       'migration:014', h.guardian_verdict_ref, now()
FROM causal_entities h
ON CONFLICT (object_type, object_id, revision) DO NOTHING;

INSERT INTO semantic_object_versions
    (object_type, object_id, revision, change, state, domain, agent_id, verdict_ref, recorded_at)
SELECT 'CausalEdge', h.id, h.revision, 'RECORDED', to_jsonb(h), h.domain,
       'migration:014', h.guardian_verdict_ref, now()
FROM causal_edges h
ON CONFLICT (object_type, object_id, revision) DO NOTHING;

INSERT INTO semantic_object_versions
    (object_type, object_id, revision, change, state, domain, agent_id, verdict_ref, recorded_at)
SELECT 'GraphFragment', h.id, h.revision, 'RECORDED', to_jsonb(h), h.domain,
       'migration:014', h.guardian_verdict_ref, now()
FROM graph_fragments h
ON CONFLICT (object_type, object_id, revision) DO NOTHING;

INSERT INTO semantic_object_versions
    (object_type, object_id, revision, change, state, domain, agent_id, verdict_ref, recorded_at)
SELECT 'Assumption', h.id, h.revision, 'RECORDED', to_jsonb(h), h.domain,
       'migration:014', NULL, now()
FROM assumptions h
ON CONFLICT (object_type, object_id, revision) DO NOTHING;


-- ---------------------------------------------------------------------------
-- The head rule. SECURITY DEFINER so the existence checks read history whatever
-- the caller's domain scope; the head row itself was already filtered by RLS
-- before the trigger fires. TG_ARGV[0] is the object type.
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION fn_semantic_head_advances()
RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
BEGIN
    IF NEW.id IS DISTINCT FROM OLD.id THEN
        RAISE EXCEPTION '% %: an object''s id cannot change', TG_ARGV[0], OLD.id
            USING ERRCODE = '42501';
    END IF;
    IF NEW.revision IS DISTINCT FROM OLD.revision + 1 THEN
        RAISE EXCEPTION
            '% %: a state change must advance the revision by one (from % to %, not %); '
            'versions are never written over', TG_ARGV[0], OLD.id, OLD.revision,
            OLD.revision + 1, NEW.revision
            USING ERRCODE = '42501';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM semantic_object_versions v
        WHERE v.object_type = TG_ARGV[0] AND v.object_id = OLD.id
          AND v.revision = OLD.revision
    ) THEN
        RAISE EXCEPTION
            '% %: revision % is not recorded in semantic_object_versions, so replacing it '
            'would lose it', TG_ARGV[0], OLD.id, OLD.revision
            USING ERRCODE = '42501';
    END IF;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION fn_semantic_head_recorded()
RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM semantic_object_versions v
        WHERE v.object_type = TG_ARGV[0] AND v.object_id = NEW.id
          AND v.revision = NEW.revision
    ) THEN
        RAISE EXCEPTION
            '% %: the head is at revision %, which is not recorded in '
            'semantic_object_versions', TG_ARGV[0], NEW.id, NEW.revision
            USING ERRCODE = '42501';
    END IF;
    RETURN NULL;
END;
$$;

REVOKE ALL ON FUNCTION fn_semantic_head_advances() FROM PUBLIC;
REVOKE ALL ON FUNCTION fn_semantic_head_recorded() FROM PUBLIC;

DROP TRIGGER IF EXISTS tg_causal_entities_head_advances ON causal_entities;
CREATE TRIGGER tg_causal_entities_head_advances
    BEFORE UPDATE ON causal_entities
    FOR EACH ROW EXECUTE FUNCTION fn_semantic_head_advances('CausalEntity');
DROP TRIGGER IF EXISTS tg_causal_entities_head_recorded ON causal_entities;
CREATE CONSTRAINT TRIGGER tg_causal_entities_head_recorded
    AFTER UPDATE ON causal_entities
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION fn_semantic_head_recorded('CausalEntity');

DROP TRIGGER IF EXISTS tg_causal_edges_head_advances ON causal_edges;
CREATE TRIGGER tg_causal_edges_head_advances
    BEFORE UPDATE ON causal_edges
    FOR EACH ROW EXECUTE FUNCTION fn_semantic_head_advances('CausalEdge');
DROP TRIGGER IF EXISTS tg_causal_edges_head_recorded ON causal_edges;
CREATE CONSTRAINT TRIGGER tg_causal_edges_head_recorded
    AFTER UPDATE ON causal_edges
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION fn_semantic_head_recorded('CausalEdge');

DROP TRIGGER IF EXISTS tg_graph_fragments_head_advances ON graph_fragments;
CREATE TRIGGER tg_graph_fragments_head_advances
    BEFORE UPDATE ON graph_fragments
    FOR EACH ROW EXECUTE FUNCTION fn_semantic_head_advances('GraphFragment');
DROP TRIGGER IF EXISTS tg_graph_fragments_head_recorded ON graph_fragments;
CREATE CONSTRAINT TRIGGER tg_graph_fragments_head_recorded
    AFTER UPDATE ON graph_fragments
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION fn_semantic_head_recorded('GraphFragment');

DROP TRIGGER IF EXISTS tg_assumptions_head_advances ON assumptions;
CREATE TRIGGER tg_assumptions_head_advances
    BEFORE UPDATE ON assumptions
    FOR EACH ROW EXECUTE FUNCTION fn_semantic_head_advances('Assumption');
DROP TRIGGER IF EXISTS tg_assumptions_head_recorded ON assumptions;
CREATE CONSTRAINT TRIGGER tg_assumptions_head_recorded
    AFTER UPDATE ON assumptions
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION fn_semantic_head_recorded('Assumption');

COMMIT;
