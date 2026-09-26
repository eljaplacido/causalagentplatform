-- ===========================================================================
-- 013_assumption_sensitivity_verdict.sql
--
-- An assumption's sensitivity result records the verdict it was recorded under.
--
-- Guardian PR-007 refuses to approve an edge unless an assumption it rests on
-- carries a sensitivity result, and the review queue's risk tiers read the same
-- results. Any valid SensitivityResult counted -- and a drafter can attach one
-- to an assumption they draft (computed_by is free text), so a made-up number
-- satisfied the gate. A result now counts only when the platform recorded it:
-- SemanticLayerRegistry.record_edge_sensitivity writes here the id of the
-- RECORD_SENSITIVITY verdict it spent, and the reading
-- (src/semantic_layer/review_risk.py recorded_sensitivity) verifies that row:
-- APPROVED, RECORD_SENSITIVITY, signed by the Guardian, issued for the edge
-- being judged, and spent. propose_draft never writes the column, so a drafted
-- assumption's result has none and does not count.
--
-- UNIQUE: one verdict records one result, so a spent verdict cannot be copied
-- onto a second assumption. NULL for every assumption drafted, and for any
-- recorded before this migration (those no longer count; record them again).
-- ===========================================================================

ALTER TABLE assumptions
    ADD COLUMN IF NOT EXISTS sensitivity_verdict_id UUID REFERENCES audit_log(id);

CREATE UNIQUE INDEX IF NOT EXISTS ux_assumptions_sensitivity_verdict
    ON assumptions (sensitivity_verdict_id);

COMMENT ON COLUMN assumptions.sensitivity_verdict_id IS
    'RECORD_SENSITIVITY verdict sensitivity_result was recorded under; NULL if drafted.';
