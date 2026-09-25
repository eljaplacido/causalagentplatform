-- ===========================================================================
-- 005_one_followup_per_decision.sql
--
-- One plan and one realization per decision.
--
-- 004 allowed any number of realizations per decision, and the calibration
-- report counted each one. Append-only kept a disappointing measurement from
-- being deleted, but not from being outvoted: post two friendlier windows for
-- the same decision and it scores two-to-one in the platform's favour. The
-- first measurement stands. Measuring again later is a reasonable thing to
-- want, and it needs its own design (a pre-declared schedule of windows, each
-- counted once), not a second row that silently joins the average.
--
-- The same holds for plans: the plan carries the copy of the prediction the
-- verdict used, and a second plan would be a second target.
-- ===========================================================================

CREATE UNIQUE INDEX IF NOT EXISTS ux_decision_followups_one_per_kind
    ON decision_followups (entry_digest, kind);
