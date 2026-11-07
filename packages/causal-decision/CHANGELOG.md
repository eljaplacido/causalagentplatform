# Changelog -- causal-decision

Earlier version numbers were development milestones inside the repository and were never
published.

## 0.7.0 -- first PyPI release (unreleased)

Needs causal-core 0.15.0. 0.6.0 was never published; this entry and the next are one release.

- **A lever nobody has tried gets an experiment:** `NO_VARIATION` joins the refusals an experiment
  can resolve, sized from the outcome's spread; a failed design check still refuses (D0 v5).
- **Paired evidence, paired experiment:** a matched-pair result is sized as matched pairs
  (`ExperimentSpec.design = "paired_units"`), exactly from the observed discordance for a binary
  outcome or from the within-pair spread.
- **Not transported:** evidence from a population other than the one the action targets caps the
  verdict at `experiment`, run in the target; evidence that states no population, for an action
  that names one, goes to a person.

## 0.6.0 (never published; folded into 0.7.0)

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
