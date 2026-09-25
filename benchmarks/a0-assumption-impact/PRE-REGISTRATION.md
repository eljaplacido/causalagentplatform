# A0 assumption-impact gate — pre-registration

**Suite:** A0, whether a revision to a causal model invalidates the verdicts made under the old one
**System under test:** `packages/causal-decision` (`causal_decision.assumptions.impact`, `.diff`)
**Registered:** 2026-09-25, version 1
**Runs:** every PR, via `pytest -m a0` in `packages/causal-decision`

## Status of this document

Written and committed before `causal_decision.assumptions` exists. No bar below was chosen by
looking at output from the code under test, because there is none yet.

## What this gate is for

The assumption registry stores causal models as immutable, versioned records, and decisions name
the version they rested on. When a model is revised — an edge added, removed, reversed, a latent
confounder declared — the question that matters is not "did the graph change" (it did) but:

> **If the new graph is true, is the estimate a past verdict rested on still an estimate of the
> effect it claimed?**

`impact(old, new, query)` answers that per decision. Getting it wrong in one direction leaves a
verdict standing on an estimate the organisation no longer believes; in the other, it flags every
revision as invalidating everything, and people learn to ignore it. Both are measured here.

For back-door identification — the strategy most decisions rest on — the question has a precise
answer: the old estimate stays valid exactly when the old adjustment set is a valid adjustment set
under the new graph. Note what that is *not*: it is not "the kernel still picks back-door", and it
is not "the kernel's new adjustment set is valid". Both of those are easy to compute, both look
plausible, and both are wrong; each is a seeded defect below.

## Design

**Graph pairs.** A random DAG over 5–8 observed nodes (edge density 0.3–0.5), with 0–2 bidirected
edges, a treatment `X` and an outcome `Y` drawn so that `X` is an ancestor of `Y` in at least
half of the cases. The revised graph applies exactly one edit, drawn uniformly from: add a
directed edge, remove a directed edge, reverse a directed edge, add a bidirected edge, remove a
bidirected edge. Edits that create a cycle, or that make `X` or `Y` latent, are redrawn. Cases are
kept only when the old graph identifies the effect by back-door (the population the bars are
about); the rest are counted and reported.

**The oracle is independent of the kernel.** The new graph is taken as true. A linear-Gaussian
structural model is drawn on it (coefficients uniform in ±[0.5, 1.5], noise variances in
[0.5, 1.5]; each bidirected edge becomes a latent standard-normal parent of both endpoints). From
the coefficient matrix `B` and noise covariance `Ω` the population covariance of every variable is
`Σ = (I − B)⁻¹ Ω (I − B)⁻ᵀ`, and the true total effect of `X` on `Y` is `[(I − B)⁻¹]_{Y,X}`.
The population regression coefficient of `X` in `Y ~ X + Z_old` is read from `Σ` restricted to
observed variables. The old estimate is **valid** under the new graph iff that coefficient equals
the total effect — checked on three independent coefficient draws, all of which must agree, with
tolerance `1e-8` (absolute, on effects of order one).

This is exact algebra, not simulation: there is no sampling error, so one disagreement is a
failure. It shares no code with the kernel's d-separation or adjustment-criterion
implementation; it uses only matrix algebra on the structural model. (Generic equality: for a
valid adjustment set the equality is an identity; for an invalid one it fails except on a
measure-zero set of coefficients, which three independent draws make negligible.)

**What `impact` must say.** For each case, `impact` returns a status. The gate reads it as a
claim about the old estimate:

| status | claim | oracle must say |
|---|---|---|
| `holds` | the old estimate is still valid under the new graph | valid |
| `invalidated` | it is not (the old set is no longer valid, or the effect is no longer identified) | invalid |
| `unverified` | the strategy is not one this function can check | — (counted, and fails the gate if it appears for a back-door case) |

**Properties** (every case, no sampling):

1. **Diff round-trip.** Applying `diff(old, new)` to `old` reproduces `new` exactly (same nodes,
   latent set, and edge multiset including kind, origin and status).
2. **Self-impact.** `impact(m, m, q)` is `holds` for every model and query the kernel identifies.
3. **Determinism.** The same inputs produce the same impact report digest.
4. **A removed edge is reported.** Every directed or bidirected edge present in `old` and absent
   from `new` appears in `diff(old, new).removed_edges` — absent edges are the strongest
   assumptions in a graph, and a diff that lost one would hide exactly the change that matters.

**Seeded defects.** Each is a plausible shortcut. The gate must fail under every one.

| id | defect | must be caught by |
|---|---|---|
| `strategy_only` | `holds` whenever the new graph is still identified by back-door, whatever the set | agreement |
| `new_set_valid` | checks the kernel's *new* adjustment set instead of the old one | agreement |
| `ignore_bidirected` | the new graph's bidirected edges are dropped before checking | agreement |
| `diff_drops_removed` | `diff` omits removed edges | property 1 or 4 |

## Pass bars

```toml
[meta]
suite = "A0"
version = 1
registered = "2026-09-25"

[cases]
seed = 20260925
n_pairs = 400                  # back-door cases kept after filtering
min_per_class = 60             # each of holds / invalidated must occur at least this often
oracle_draws = 3
oracle_tolerance = 1e-8

[agreement]
must_match = 1.0               # impact status vs oracle, on every back-door case
unverified_allowed = 0         # on back-door cases

[properties]
violations_allowed = 0

[seeded_defects]
required = ["strategy_only", "new_set_valid", "ignore_bidirected", "diff_drops_removed"]
```

**Why `min_per_class`.** A gate whose cases were all `holds` would pass an `impact` that always
says `holds`. The class balance is a scope floor: the gate must have looked at both answers.

**What this gate does not establish.**

- Anything about front-door, IV, proximal or general-ID decisions. For those, `impact` returns
  `holds` only when the new graph yields the identical certificate (same strategy, same
  instruments, mediators or proxies), and `unverified` otherwise. That is conservative by
  construction, and its false-alarm rate is reported, not gated.
- Whether the new graph is right. The gate assumes the revision is the better belief and asks
  what follows; which graph to believe is the reviewers' question.
- Nonlinear or non-Gaussian models. Validity of an adjustment set is a graphical property, so the
  linear oracle tests the right thing, but the *size* of the bias an invalid set introduces is
  not measured here.

## Changelog

- v1 (2026-09-25): initial registration.
