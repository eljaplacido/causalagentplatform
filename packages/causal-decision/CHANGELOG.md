# Changelog -- causal-decision

Earlier version numbers were development milestones inside the repository and were never
published.

## 0.6.0 -- first PyPI release (unreleased)

- **Decide:** evidence and a policy to one of `act`, `act_with_approval`, `experiment` or
  `refuse`; a refusal is never an action, and a contested estimate cannot justify a confident one.
- **For whom:** a segment the action materially harms blocks `act`, judged on the family-wise
  interval.
- **Unreviewed LLM-proposed edges** cap the verdict at `experiment` (contract 0.14.0).
- **Keep the decision:** a hash-chained decision ledger with checkpoints, and review dates.
- **Keep the assumptions:** versioned causal graphs with role-gated review, and `impact(old, new,
  query)` for a revision.
- **Score the decisions:** outcome follow-up plans and a calibration report whose rates carry
  Clopper-Pearson intervals.
- **Evidence:** the pre-registered gates D0, A0 and C0, each with seeded defects.
