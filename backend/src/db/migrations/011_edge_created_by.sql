-- ===========================================================================
-- 011_edge_created_by.sql
--
-- causal_edges gets the created_by column its siblings have.
--
-- causal_entities and graph_fragments record who drafted them (created_by,
-- stamped from the token by POST /api/semantic/drafts); causal_edges never did,
-- so the governance review queue could not say who submitted an edge (register
-- review-queue-risk-tier, septa-36). Nullable: edges drafted before this have
-- no recorded submitter, and the queue says so rather than inventing one.
-- ===========================================================================

ALTER TABLE causal_edges
    ADD COLUMN IF NOT EXISTS created_by JSONB;

COMMENT ON COLUMN causal_edges.created_by IS
    'UserRef of the user who drafted the edge; NULL for edges drafted before 011.';
