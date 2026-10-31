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
- **Shadow decisions:** `DecisionRecord.shadow`, a sealed shadow tag (left out of the digest when
  absent, so earlier chains verify unchanged); `causal_decision.shadow` (`emittable`, which refuses
  a shadow record, and `select` for listing real and shadow apart); `past_review` keeps the newest
  decision per action and tag, so a shadow decision never retires a real one.
- **Shadow rounds:** `causal_decision.shadow_round` seals a round's studies through the kernel as
  shadow decisions and scores them against the round's pre-registered bars.
