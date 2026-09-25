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
| Retrieve the assumptions and evidence that apply | Done for causal models: `causal_decision.assumptions` and `/api/v1/models` keep each model as immutable, content-addressed versions whose edges carry origin and review status, and a decision names the version it used. Missing: retrieving evidence (documents, metrics) for an edge; nothing links it |
| Decide identified / partially identified / unsupported | Done, except bounds: complete ID, back-door with the optimal adjustment set, front-door, conditional IV, proximal; 11 typed refusals, each with what is missing |
| Estimate impact and uncertainty | Done for ATE and ATT with coverage-tested intervals, and for effects within declared segments with family-wise correction and a heterogeneity test (pre-registered T4), and for staggered rollouts by difference-in-differences with unit-clustered intervals and a pre-trends test (pre-registered T5). and for watched randomized rollouts by anytime-valid confidence sequences (pre-registered T6). and for cluster-randomized rollouts with cluster-robust intervals (T7). Missing: policy value, switchback designs and spillovers, DiD with covariates, and subgroup *discovery* |
| Robustness checks | Done: placebo, negative controls, graph-implied independences with measured power, overlap, balance, E-values, robustness values |
| Decide: act, ask, experiment, refuse | Done: `packages/causal-decision` turns an outcome, a request and a policy into one verdict, deterministically, gated by the pre-registered D0 suite (v3: 19 scenarios, 8 properties, 8 seeded defects). Declared segments reach the verdict -- an action that harms one of them is not automated on a favourable pooled effect -- and so does a difference-in-differences result's parallel-trends breakdown: a conclusion a violation smaller than the pre-adoption ones would flip is sent to an experiment |
| Evidence record | Partial: every result and every decision carries digests of its inputs and itself, decisions are persisted into a verified hash chain with the observed outcome recorded against them, and the model version a decision used is recorded. Checkpoints of the chain head can be taken and verified against (`/api/v1/decisions/checkpoint`, `/verify-anchored`). Missing: publishing a checkpoint somewhere the writer cannot edit |
| Recommend the next experiment | Done for the case the layer can size: randomized units, with the minimum detectable effect, per-arm size, duration and guardrails. Clustered, switchback and sequential designs are not sized |

## Order of work

1. ~~**Decision policy.**~~ **Done** (`packages/causal-decision`, `causal_decide` over MCP). Risk
   classes and pre-registered thresholds over what the kernel already returns → `act`,
   `act_with_approval`, `experiment`, `refuse`. "Do nothing" is always a candidate. Deterministic,
   with seeded defects. What it does *not* settle: whether the thresholds are the right ones.
   That needs step 5.
2. **Evidence record and assumption registry.** *Done, except anchoring.* A Guardian verdict is
   signed, names the object it authorises, and is spent on use; both ways to mint one outside the
   Guardian are closed; every decision is sealed into a hash chain with the digests of the
   evidence it rested on. The assumption registry keeps causal models as immutable versions
   (`causal_decision.assumptions`, `/api/v1/models`): edges carry their origin and review status,
   a revision cannot approve an edge, nobody reviews an edge they introduced, a decision names the
   version it used, and `impact` says which recorded verdicts a revision invalidates —
   pre-registered as A0 against an oracle independent of the kernel. `causal_impact` asks the same
   question over MCP. Anchoring is half done: the chain head can be checkpointed and the chain
   verified against checkpoints kept elsewhere; nothing yet publishes a checkpoint outside the
   writer's control (`decision-ledger-not-anchored`).
3. **The estimators operations actually need.** Segment-level effects are **done**
   (`causal_core.segments`, pre-registered T4: declared subgroups, Bonferroni family-wise
   intervals, Holm across the family, one Cochran's Q before anything is compared). Staggered
   difference-in-differences and event studies are **done** (`causal_core.panel`, pre-registered
   T5: group-time effects against never-treated units, unit-clustered influence-function
   intervals, a pre-trends falsification test, tier C). Anytime-valid monitoring of
   randomized canaries is **done** (`causal_core.sequential`, pre-registered T6: asymptotic
   confidence sequences over a predictable-baseline AIPW score). Cluster-randomized designs are
   **done** (`causal_core.cluster`, pre-registered T7: CR2 intervals on t(K − 2), a refusal where
   a few clusters dominate). Still to come: switchback designs and spillovers between units, and DiD with covariates
   (parallel-trends sensitivity is **done**: intervals under a relative-magnitudes restriction and
   a breakdown multiple, T5 v3). Each with its own
   pre-registered gate and a seeded defect.
4. ~~**Experiment recommendation.**~~ **Done for randomized units**, in the same package: unit,
   per-arm size and duration from a minimum-detectable-effect calculation, guardrails. A refusal
   now carries the outcome's spread even when identification is what failed, so an unanswerable
   question can still be sized. Clustered, switchback and sequential designs are step 3's.
5. **Outcome capture and calibration.** *Machinery built; evidence not yet gathered.* Every
   decision now seals a follow-up plan carrying the prediction it used;
   `POST /api/v1/decisions/{entry_digest}/followup` re-runs that plan's own analysis over a
   later window and scores it; `GET /api/v1/decisions/calibration` aggregates. The hard part
   was not the arithmetic: calibrating a causal prediction needs a causal estimate afterwards,
   and an action rolled out to everybody leaves no comparison group. That case is the kernel's
   own refusal, recorded as an undecidable measurement and counted beside the rates rather than
   dropped — those decisions are not missing at random. What remains is operational history:
   until follow-ups have been taken, every rate reads null, and the thresholds are consistent
   rather than calibrated (`decision-thresholds-uncalibrated`).
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
