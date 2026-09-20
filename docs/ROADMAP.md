# Roadmap

> Status of this document: a plan, not a description of the code. What exists today is in
> [`CURRENT_STAGE.md`](CURRENT_STAGE.md), where every row links to the test that pins it.

## What this is for

**A causal decision assurance layer for agentic workflows.** It sits between an agent's proposed
action and the systems that action would change, and answers one question:

> If we do this action, for this population, over this horizon, what evidence says it will move the
> target outcome — and is that evidence strong enough to act on without asking a human?

The layer's job is not to be clever. It is to be trustworthy about what it does not know: to refuse,
to say what is missing, and to propose the experiment that would settle the question.

First market: internal operational decisions (incident remediation, change gating, ticket routing,
contact strategy), where actions are frequent, outcomes are measurable, interventions are reversible
or approval-gated, and the cost of wrong recommendations is already felt. Energy flexibility and
sustainability investment assurance follow, in that order.

## Where the code is against that

| Capability the layer needs | Today |
|---|---|
| A typed causal question | Partial: `CausalQuery` is typed, but `population` and `horizon` are free text, and nothing translates an agent's action into an estimand |
| Retrieve the assumptions and evidence that apply | Missing: edges carry origin, status and evidence in the contract, but nothing stores, versions or retrieves them |
| Decide identified / partially identified / unsupported | Done, except bounds: complete ID, back-door with the optimal adjustment set, front-door, conditional IV, proximal; 11 typed refusals, each with what is missing |
| Estimate impact and uncertainty | Done for ATE and ATT with coverage-tested intervals; missing segment effects, difference-in-differences, policy value, sequential tests, interference-aware designs |
| Robustness checks | Done: placebo, negative controls, graph-implied independences with measured power, overlap, balance, E-values, robustness values |
| Decide: act, ask, experiment, refuse | Missing. The kernel returns the evidence for a verdict; nothing turns it into one |
| Evidence record | Partial: every result carries digests and its refutations; nothing is persisted, no approval binding, no observed outcome |
| Recommend the next experiment | Missing |

## Order of work

1. **Decision policy.** Risk classes and pre-registered thresholds over what the kernel already
   returns (identification tier, interval versus decision threshold, E-value versus plausible
   confounding, refuter status, reversibility, cost) → `auto`, `approve`, `experiment first`,
   `refuse`. "Do nothing" is always a candidate. Deterministic, tested, with seeded defects.
2. **Evidence record and assumption registry.** Persist request → certificate → result → verdict →
   approver, hash-chained; version graphs and the provenance of every edge. Fix the Guardian
   (today `/check` can never return APPROVED, and verdicts can be forged and replayed).
3. **The estimators operations actually need.** Segment-level effects, staggered
   difference-in-differences and event studies, anytime-valid sequential tests for canaries,
   cluster and switchback designs for interference. Each with its own pre-registered gate and a
   seeded defect.
4. **Experiment recommendation.** Turn a refusal into a concrete minimal experiment: randomization
   unit, duration from a minimum-detectable-effect calculation, guardrails. The refusals already
   carry why the question could not be answered.
5. **Outcome capture and calibration.** Compare predicted with realized effects; publish regret and
   calibration. This is the ROI evidence.
6. **Domain packs.** Operations first, then energy flexibility.

## What this project will not do first

- Causal discovery as the product. It is underidentified and hard to validate; it stays a
  supporting capability.
- "An LLM that discovers causality." The LLM proposes and narrates; statistics decide.
- Autonomous consequential action. The layer recommends, gates and records; a human holds
  irreversible decisions until the calibration evidence says otherwise.
- A general causal foundation model.

## How anything here becomes a claim

Every capability above leaves
[`conformance/declared_not_implemented.json`](../conformance/declared_not_implemented.json) only by
being implemented and pinned by a test, with a pre-registered benchmark where a number is claimed.
Bars are committed before the run that measures them, and failing runs stay in the repository.
