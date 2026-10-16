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
| Estimate impact and uncertainty | Done for ATE and ATT with coverage-tested intervals; for effects within declared segments with family-wise correction and a heterogeneity test (pre-registered T4); for staggered rollouts by difference-in-differences with unit-clustered intervals and a pre-trends test, against never-treated or not-yet-treated units and, with covariates, doubly robust (T5); for watched randomized rollouts by anytime-valid confidence sequences (T6); for cluster-randomized rollouts with CR2 cluster-robust intervals (T7); and for switchbacks, where the caller chooses a washout that covers the carryover (T8). Missing: policy value, spillovers between units, DiD with anticipation or repeated cross-sections, a carryover check with real power, and subgroup *discovery* |
| Robustness checks | Done: placebo, negative controls, graph-implied independences with measured power, overlap (honest covariate regions and a limited-overlap flag, T10), balance, E-values, robustness values, and Rosenbaum bounds on a matched design for back-door designs with a binary treatment (T9). Missing: a sensitivity model for the weighted estimators themselves |
| Decide: act, ask, experiment, refuse | Done: `packages/causal-decision` turns an outcome, a request and a policy into one verdict, deterministically, gated by the pre-registered D0 suite (v4: 22 scenarios, 8 properties, 9 seeded defects). Declared segments reach the verdict -- an action that harms one of them is not automated on a favourable pooled effect -- and so does a difference-in-differences result's parallel-trends breakdown: a conclusion a violation smaller than the pre-adoption ones would flip is sent to an experiment. An unreviewed edge a language model proposed sends the decision to an experiment too (v4) |
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
   writer's control (`decision-ledger-not-anchored`; on the ledger as `septa-11`).
3. **The estimators operations actually need.** Segment-level effects are **done**
   (`causal_core.segments`, pre-registered T4: declared subgroups, Bonferroni family-wise
   intervals, Holm across the family, one Cochran's Q before anything is compared). Staggered
   difference-in-differences and event studies are **done** (`causal_core.panel`, pre-registered
   T5: group-time effects against never-treated units, unit-clustered influence-function
   intervals, a pre-trends falsification test, tier C), and since T5 v4 against not-yet-treated
   units and, with time-invariant covariates, doubly robust under conditional parallel trends. Anytime-valid monitoring of
   randomized canaries is **done** (`causal_core.sequential`, pre-registered T6: asymptotic
   confidence sequences over a predictable-baseline AIPW score). Cluster-randomized designs are
   **done** (`causal_core.cluster`, pre-registered T7: CR2 intervals on t(K − 2), a refusal where
   a few clusters dominate). Switchback designs are **done** (also `causal_core.cluster`,
   pre-registered T8: a uniform washout, block means, Welch on t(K − 2); since T8 v2 a
   carryover check with gated power makes the result contested when the effect is still moving
   after the washout, and misses a carryover too small next to the standard error). Parallel-trends
   sensitivity is **done** (intervals under a relative-magnitudes restriction and a breakdown
   multiple, T5 v3). Still to come, in the ledger's order: spillovers between randomized units
   (`septa-7`); then the DiD work the register still lists --
   the full Rambachan–Roth sensitivity (`did-rambachan-roth`), and anticipation windows,
   repeated cross-sections and time-varying covariates (`did-anticipation-cross-sections`).
   Each with its own pre-registered gate and a seeded defect.
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
   dropped — those decisions are not missing at random. The report that would judge the
   evidence is built and gated (`septa-10`, the C0 gate, on simulated streams): a rate only from
   ten follow-ups or more, each with a Clopper-Pearson interval, undecidable follow-ups carried
   into worst-case bounds, and "could not resolve" kept apart from "no difference". What remains
   is operational history: no follow-up has been taken on a real operation, so every rate is
   refused, and the thresholds are consistent rather than calibrated
   (`decision-thresholds-uncalibrated`). Every decision is also sealed with a review date (90 days
   by default), and the report lists those past it that nothing has re-checked (`septa-102`):
   evidence about one population and period does not stand forever, though nothing yet stops an
   executor acting on a decision past its date.
6. **Domain packs.** Operations first, then energy flexibility. Not yet on the ledger.

## What is next

The order of work above is the direction. The next items come from the work ledger,
`.beads/issues.jsonl`; `septacore ready` in the repository root lists what is ready, highest
priority first, and the priorities put hardening the platform before new estimators:

- **p0, the platform's own trust boundary.** Done: the Guardian computes the PR-002 score itself,
  from the prose and the kernel outcome it reports, instead of reading it from the caller
  (`septa-25`); every state-changing route is checked against one role matrix, and the token's
  `domains` claim bounds what a user sees of the semantic layer, at the application layer
  (`septa-26`), and on PostgreSQL at the database layer too, by row-level security
  (`septa-47`); the governance page's Approve, which could never succeed, now
  names the object and the approver's classification (`septa-46`); the gate runs bandit, a
  secret scan, pip-audit and npm audit, and every advisory they found was fixed rather than
  excused (`septa-27`); CI installs every Python environment from a hash-pinned lock that the
  gate holds current, scans both container images with Trivy, and attaches a CycloneDX SBOM to
  each release, and Dependabot opens update PRs (`septa-49`; DAST is not built, register
  `dast-not-built`). Every secret is
  read from its variable or a mounted `<NAME>_FILE`, the backend refuses to start outside
  `ENV=local` without a signing key, and the key rotates through a previous-keys list
  (`septa-28`; no secret-manager client). The backend suite runs on PostgreSQL as well as
  SQLite, on the schema the migrations build: it found that the Guardian could not write
  `audit_log` there and that `session_state` and `incidents` had no migration, and both are
  fixed (`septa-52`, gate step `backend-pytest-postgres`). No SQL in `backend/src` is built
  from strings, and bandit enforces it (`septa-50`). Approving a draft needs an APPROVED
  Guardian verdict issued for `GOVERNANCE_APPROVE`, not one from a check for any other action
  (`septa-60`), and rejecting one needs an APPROVED verdict for `GOVERNANCE_REJECT` on that
  draft; the governance page sends the reviewer's classification with both. Rejecting used to
  need a REJECTED verdict, which a classified reject request never got (`septa-53`).
- **p1, release and drift.** Done: the gate checks that the skills and agent docs name only
  things that exist (`septa-3`), and the MCP tools' parameters are derived from `CausalQuery`,
  with a parity test (`septa-4`; it found `causal_impact` judging every earlier answer as
  observational, and `causal_decide` dropping `treatment_levels`). Open: protect `main`
  (`septa-1`; needs a public repository or a paid plan); a first PyPI release of the three
  packages (`septa-2`); SeptaCore's optional stages that fit (`septa-5`). Also done: the legacy
  MCP server, `backend/mcp_server`, deleted; `packages/causal-mcp` is the one server (`septa-6`).
- **p2, the kernel and decision layer.** Done: DiD with covariates (doubly robust) and
  not-yet-treated comparisons, T5 v4 (`septa-8`); a switchback carryover check with gated power
  whose flag makes the result contested, T8 v2 (`septa-9`; a carryover too small to bias the
  estimate materially stays undetectable at T8's sizes, and is reported); Guardian PR-007, no
  edge approved without a sensitivity result stored on an assumption it rests on (`septa-33`; since `062f57d` only one the
  platform recorded for the edge counts, not a drafter's own), and the kernel's sensitivity
  analysis of a draft edge recorded there when the platform runs it (`septa-70`); Rosenbaum bounds on
  a matched design, T9 (`septa-12`); positivity detection by honest overlap regions and a
  limited-overlap flag, T10 and T2 v2 (`septa-34`; four-way strata and exclusive-or patterns stay
  undetected, and are recorded); triangulation at moderate n through an outcome-model
  specification test, T0 v4 (`septa-35`; it also flags nonlinearity that does not bias the
  estimate);
  the review queue ordered by a risk tier derived from stored fields, with who submitted each
  item (`septa-36`). Open: step 3's remaining estimator (`septa-7`), calibration evidence (`septa-10`: the report
  and its simulated C0 gate are built; real follow-ups are not),
  and anchoring (`septa-11`). Real follow-ups are to come from **shadow runs**: studies on real
  decisions, proposed by devbrain and agentcenter, with verdicts sealed and not acted on. Their
  context pack is built (`shadow/`, `septa-94`). Still to build: a runtime shadow mode that
  never emits (`septa-95`), and a round runner with a scorecard (`septa-96`). Metamorphic tests
  of the kernel are built (`septa-99`): they check that renaming, rescaling, reordering and
  duplicating the data change the answer only as they must. Two pieces of test work are queued:
  - a gated browser test of `/analyze` (`septa-97`);
  - a G0 gate for an LLM agent driving causal-mcp (`septa-98`).
- **p3 and p4.** Done: provenance is durable and append-only, with erasure as a tombstone, and
  session runs, Guardian checks and decisions write it (`septa-48`, `septa-16`); semantic objects
  are versioned, never rewritten, and carry lineage from source data to approver (`septa-14`,
  `septa-15`); the LLM layer has its first callers, narration from versioned prompt templates
  kept only when grounded (`septa-17`, `septa-37`). The rest of the register (versioned
  interventions, lineage onward to analyses, Cynefin routing, frontend views and others), then
  the longer-range
  methods: discovery, rung-3 counterfactuals, general ID estimation, subgroup discovery. Many of
  these end either in the capability or in its claim being withdrawn from the docs.
  Four have ended that way: EconML/PyMC backends, the `cynepic-*` Rust crates, Neo4j as the
  causal graph store and Kafka streaming were claimed and never built, and are not needed by the
  product, so the claims were withdrawn and the idle Kafka and Redis services removed
  (`econml-pymc`, `rust-crates`, `neo4j-graph-store`, `kafka-streaming` in `left_the_list`).
  Of the frontend views, the analysis page (`/analyze`: data, question, graph checked live,
  answer, decision; `septa-91`), the decision ledger (`/decisions`) and the assumption registry
  (`/registry`) are built (`septa-24`), and the owner's second brain (agentcenter) feeds
  suggestions in and receives recorded decisions (`septa-92`); the DAG Workbench proposes a fragment through the governed
  draft route but stays `partial` (`dag-workbench`): it authors no new edges, and its e2e spec
  stubs the backend.

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
