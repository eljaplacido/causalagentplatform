-- ===========================================================================
-- 010_rls_binds.sql
--
-- Row-level security that binds: domain isolation enforced by the database as
-- a second line behind the application's (src/middleware/rbac.py).
--
-- What 001 declared and why it bound nothing:
--   * nothing set app.user_domains or app.user_role, so the policies had no
--     scope to read;
--   * the app connected as the tables' owner (a superuser in docker-compose),
--     and RLS does not apply to either;
--   * the domain list was parsed with string_to_array(..., ','), so a domain
--     whose name held a comma split into two, and
--   * `app.user_role IN ('governance_officer', 'platform_engineer')` let those
--     roles see every domain even when their token carried a `domains` claim,
--     which rbac.domain_scope says binds every role;
--   * graph_fragments had no policy at all.
--
-- The contract with the application (src/db/scope.py sets both, per
-- transaction, with set_config(..., true), so a pooled connection never
-- carries another request's scope):
--
--   app.user_domains  a JSON array of domain names, or '' / unset for
--                     "no domain restriction". The application sends '' only
--                     where rbac.domain_scope returned None.
--   app.user_role     a JSON array of the caller's platform roles (only roles
--                     rbac.ALL_ROLES knows), or ["causal_service"] for an
--                     explicitly declared service context. causal_service is
--                     not a platform role: a token cannot carry it through.
--
-- A row is visible (and writable) when its domain is in app.user_domains, or
-- when app.user_domains is unset AND a role in app.user_role is one the
-- platform leaves unscoped (governance_officer, platform_engineer) or is the
-- service marker. So the database applies rbac's rule itself: a present claim
-- binds every role, an absent one frees only the unscoped roles, and a caller
-- with nothing set sees nothing. A malformed setting is an error, not a pass.
--
-- The app connects as causal_app, which owns nothing and is neither superuser
-- nor BYPASSRLS; src/db/scope.py refuses to start on a connection that would
-- bypass these policies. FORCE ROW LEVEL SECURITY is deliberately not set: the
-- owner is the migration and operations role, and keeping it unbound is what
-- lets it repair data; the application never connects as it.
-- ===========================================================================

BEGIN;

-- ---------------------------------------------------------------------------
-- The scope, parsed once.
-- ---------------------------------------------------------------------------

-- The domains the current transaction may see, or NULL when app.user_domains
-- is unset or empty (no domain restriction was asserted). Not-an-array raises.
CREATE OR REPLACE FUNCTION app_scope_domains()
RETURNS TEXT[] LANGUAGE sql STABLE AS $$
    SELECT CASE
        WHEN NULLIF(current_setting('app.user_domains', true), '') IS NULL THEN NULL
        ELSE ARRAY(
            SELECT jsonb_array_elements_text(
                current_setting('app.user_domains', true)::jsonb
            )
        )
    END
$$;

-- True only when no domain list was asserted AND the caller holds a role the
-- platform leaves unscoped, or is the declared service context.
CREATE OR REPLACE FUNCTION app_scope_unscoped()
RETURNS BOOLEAN LANGUAGE sql STABLE AS $$
    SELECT NULLIF(current_setting('app.user_domains', true), '') IS NULL
       AND COALESCE(
               NULLIF(current_setting('app.user_role', true), '')::jsonb
                   ?| ARRAY['governance_officer', 'platform_engineer', 'causal_service'],
               false
           )
$$;

-- Whether a row of `row_domain` is inside the current scope.
CREATE OR REPLACE FUNCTION app_scope_allows(row_domain TEXT)
RETURNS BOOLEAN LANGUAGE sql STABLE AS $$
    SELECT app_scope_unscoped()
        OR row_domain = ANY(COALESCE(app_scope_domains(), ARRAY[]::TEXT[]))
$$;

COMMENT ON FUNCTION app_scope_allows(TEXT) IS
    'RLS predicate: the row''s domain is in app.user_domains (JSON array), or no '
    'list is set and app.user_role holds an unscoped role or causal_service.';


-- ---------------------------------------------------------------------------
-- The policies: 001's are replaced, graph_fragments gets its first.
-- USING filters what is read, updated and deleted; WITH CHECK refuses an
-- INSERT or UPDATE that would put a row outside the caller's domains.
-- ---------------------------------------------------------------------------

DROP POLICY IF EXISTS causal_entities_domain_isolation ON causal_entities;
CREATE POLICY causal_entities_domain_isolation
    ON causal_entities
    FOR ALL
    USING (app_scope_allows(domain))
    WITH CHECK (app_scope_allows(domain));
ALTER TABLE causal_entities ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS causal_edges_domain_isolation ON causal_edges;
CREATE POLICY causal_edges_domain_isolation
    ON causal_edges
    FOR ALL
    USING (app_scope_allows(domain))
    WITH CHECK (app_scope_allows(domain));
ALTER TABLE causal_edges ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS graph_fragments_domain_isolation ON graph_fragments;
CREATE POLICY graph_fragments_domain_isolation
    ON graph_fragments
    FOR ALL
    USING (app_scope_allows(domain))
    WITH CHECK (app_scope_allows(domain));
ALTER TABLE graph_fragments ENABLE ROW LEVEL SECURITY;


-- ---------------------------------------------------------------------------
-- Grants: what causal_app lacked for what the app does.
--
-- 001 granted USAGE on the sequences that existed then; decision_followups.id
-- (004, BIGSERIAL) came later, so an INSERT into it as causal_app failed on
-- nextval. Nothing is widened beyond that: causal_app still has no DELETE or
-- TRUNCATE anywhere, and no UPDATE on the append-only tables, whose triggers
-- stay in place.
-- ---------------------------------------------------------------------------

GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO causal_app;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO causal_governance;

COMMIT;
