-- ===========================================================================
-- 006_graph_fragment_lineage.sql
--
-- graph_fragments gets the lineage column its siblings have.
--
-- promote_to_approved and reject_draft serve CausalEdge, CausalEntity and
-- GraphFragment alike, and both read and append `lineage`. causal_edges and
-- causal_entities have had the column since 001; graph_fragments never did, so
-- approving or rejecting a fragment failed on its first SELECT, in production
-- and under test alike. No test had ever taken that path.
-- ===========================================================================

ALTER TABLE graph_fragments
    ADD COLUMN IF NOT EXISTS lineage JSONB NOT NULL DEFAULT '[]';

COMMENT ON COLUMN graph_fragments.lineage IS
    'LineageRecord[]: one step per approval or rejection, appended by the registry.';
