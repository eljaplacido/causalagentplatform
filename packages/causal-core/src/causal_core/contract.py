"""
The causal claim contract: the typed objects every surface speaks.

A library call, an MCP tool, a REST route and a UI all exchange these same
objects, and each one ends in exactly one of three outcome kinds:

  * ``result``            -- an estimate was produced (:class:`CausalResult`);
  * ``refusal``           -- the question cannot be answered honestly from what
                             was supplied (:class:`Abstention`), which is a
                             correct answer, not an error;
  * ``apparatus_failure`` -- something broke while trying (:class:`ApparatusFailure`).

There is no fourth, composite verdict. Numbers are never NaN or infinite, a
confidence interval is either computed or ``None`` with a reason, and every
result carries the digests needed to reproduce it.

JSON Schemas generated from these models are committed under
``conformance/causal-claims/`` and pinned by a test, so a change here that is
not also made there fails CI.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

CONTRACT_VERSION = "0.14.0"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


# ── Enumerations ──────────────────────────────────────────────────────────────


class Rung(StrEnum):
    """Pearl's ladder. A query must declare which rung it asks about."""

    association = "association"
    intervention = "intervention"
    counterfactual = "counterfactual"


class Design(StrEnum):
    observational = "observational"
    randomized = "randomized"
    #: Panel data, staggered adoption of an absorbing binary treatment; needs
    #: ``CausalQuery.unit`` and ``CausalQuery.time``. Identified by parallel trends and no
    #: anticipation -- assumptions outside the graph, so tier C (see causal_core.panel).
    difference_in_differences = "difference_in_differences"


class EdgeOrigin(StrEnum):
    """Who or what put an edge in the graph."""

    human = "human"
    data = "data"
    experiment = "experiment"
    literature = "literature"
    llm_prior = "llm_prior"


class EdgeStatus(StrEnum):
    """Review state of an edge.

    ``asserted`` is a statement by whoever supplied the graph. ``proposed`` is a
    suggestion (the default for ``llm_prior`` edges) that has not been reviewed.
    ``refuted`` edges are dropped before analysis.
    """

    asserted = "asserted"
    proposed = "proposed"
    approved = "approved"
    refuted = "refuted"


class IdentificationStatus(StrEnum):
    identified = "identified"
    not_identified = "not_identified"


class Strategy(StrEnum):
    randomized = "randomized"
    backdoor = "backdoor"
    frontdoor = "frontdoor"
    iv = "iv"
    proximal = "proximal"
    id_general = "id_general"
    difference_in_differences = "difference_in_differences"


class Tier(StrEnum):
    """Strength of identification.

    A -- identified nonparametrically from the graph alone.
    B -- identified only under additional parametric or structural assumptions
         that the graph cannot express (e.g. effect homogeneity for IV).
    C -- reserved: identified only by an assumption outside the graph.
    D -- not identified.
    """

    A = "A"
    B = "B"
    C = "C"
    D = "D"


class AbstentionReason(StrEnum):
    NON_IDENTIFIABLE = "NON_IDENTIFIABLE"
    POSITIVITY = "POSITIVITY"
    LOW_N = "LOW_N"
    UNAPPROVED_ASSUMPTION = "UNAPPROVED_ASSUMPTION"
    CI_TOO_WIDE = "CI_TOO_WIDE"
    DESIGN_CHECK_FAILED = "DESIGN_CHECK_FAILED"
    RUNG_MISMATCH = "RUNG_MISMATCH"
    INVALID_ADJUSTMENT_SET = "INVALID_ADJUSTMENT_SET"
    WEAK_INSTRUMENT = "WEAK_INSTRUMENT"
    NO_ESTIMATOR = "NO_ESTIMATOR"
    INVALID_INPUT = "INVALID_INPUT"


class ResultStatus(StrEnum):
    """How much weight a produced estimate can bear.

    supported   -- identified, estimators agree, no falsification test failed.
    provisional -- as supported, but the graph contains unreviewed edges.
    contested   -- a falsification test failed, estimators disagree beyond sampling
                   noise, or a switchback's carryover outlasts its washout. The numbers
                   are reported; causal language is not.
    """

    supported = "supported"
    provisional = "provisional"
    contested = "contested"


# ── Graph ─────────────────────────────────────────────────────────────────────


class Edge(_Model):
    source: str
    target: str
    kind: Literal["directed", "bidirected"] = "directed"
    origin: EdgeOrigin = EdgeOrigin.human
    status: EdgeStatus = EdgeStatus.asserted
    evidence: tuple[str, ...] = ()


class CausalGraph(_Model):
    """A causal graph over named variables.

    Directed edges are causal claims. A bidirected edge ``a <-> b`` says an
    unmeasured common cause of ``a`` and ``b`` may exist. Nodes listed in
    ``latent`` are unmeasured: they may appear in edges but never in data.
    The edges that are *absent* are the strongest assumptions in the graph.
    """

    nodes: tuple[str, ...]
    edges: tuple[Edge, ...]
    latent: tuple[str, ...] = ()


# ── Query and policy ──────────────────────────────────────────────────────────


class CausalQuery(_Model):
    rung: Rung = Rung.intervention
    treatment: str
    outcome: str
    estimand: Literal["ate", "att"] = Field(
        default="ate",
        description="ate: average effect over the population. att: average effect on the "
        "treated units (binary treatment only; needs overlap in one direction only).",
    )
    design: Design = Design.observational
    treatment_levels: tuple[float, float] | None = Field(
        default=None,
        description="(control, treated). Defaults to (0, 1) for a binary treatment; for a "
        "continuous treatment the effect is reported per unit unless levels are given.",
    )
    adjust_for: tuple[str, ...] | None = Field(
        default=None,
        description="A user-chosen adjustment set. It is validated against the graph and "
        "refused if it is not a valid adjustment set.",
    )
    instruments: tuple[str, ...] | None = None
    treatment_proxies: tuple[str, ...] = ()
    outcome_proxies: tuple[str, ...] = ()
    negative_control_outcomes: tuple[str, ...] = ()
    expected_treated_share: float | None = Field(default=None, gt=0, lt=1)
    segment_by: tuple[str, ...] = Field(
        default=(),
        description="Discrete, pre-treatment columns to estimate the effect within, declared "
        "before the answer is seen. The kernel does not search for subgroups: finding the "
        "subgroup with the largest effect is a different activity with different statistics, "
        "and reporting it as if it had been declared is how a team fools itself.",
    )
    cluster: str | None = Field(
        default=None,
        description="Randomized designs only: the column identifying the unit of randomization "
        "(service, region, tenant) when outcomes are measured on finer units (requests, users). "
        "Standard errors are then cluster-robust; treatment must be constant within a cluster.",
    )
    washout: float | None = Field(
        default=None,
        ge=0,
        description="Switchback designs (cluster = the time block, time = within-block time): "
        "drop, from every block, the observations within this many time units of its start "
        "-- the settling-in after a switch. Every block, not only switched ones, so a block's "
        "retained window never depends on its neighbour's arm.",
    )
    sequence: str | None = Field(
        default=None,
        description="Randomized designs only: the column giving each unit's arrival order. When "
        "set, the effect is reported as an anytime-valid confidence sequence at every look, so "
        "the analysis may be read -- and stopped -- as often as wanted without inflating its "
        "error rate. Needs `expected_treated_share`, the known allocation probability.",
    )
    unit: str | None = Field(
        default=None,
        description="Panel designs only: the column identifying the unit observed repeatedly.",
    )
    time: str | None = Field(
        default=None,
        description="Panel designs only: the column holding the period, as an integer or an "
        "ordered value. Periods are the distinct values in sorted order.",
    )
    covariates: tuple[str, ...] = Field(
        default=(),
        description="Panel designs only: time-invariant, pre-treatment columns under which "
        "parallel trends is assumed to hold; each group-time effect is then doubly robust "
        "(Sant'Anna & Zhao 2020). Covariates that vary within a unit, and poor overlap, are "
        "refused.",
    )
    control_group: Literal["never_treated", "not_yet_treated"] = Field(
        default="never_treated",
        description="Panel designs only: never_treated compares each cohort with units never "
        "treated; not_yet_treated adds cohorts not yet treated at that period, for rollouts "
        "that reach every unit.",
    )
    population: str | None = None
    horizon: str | None = None


class AnalysisPolicy(_Model):
    """Every threshold that can change an answer. Hashed into provenance."""

    ci_level: float = Field(default=0.95, gt=0.5, lt=1)
    min_n: int = Field(default=30, ge=2)
    min_per_arm: int = Field(default=10, ge=2)
    propensity_bounds: tuple[float, float] = (0.01, 0.99)
    max_extreme_propensity_share: float = Field(default=0.05, ge=0, le=1)
    overlap_region_bound: float = Field(
        default=0.05,
        ge=0,
        lt=0.5,
        description="Binary treatments with an adjustment set: a covariate region is a positivity "
        "violation for an arm when the upper confidence bound on that arm's probability there, "
        "from held-out units, is below this. 0 turns the region check off. A violation share "
        "above max_extreme_propensity_share refuses; a smaller one is warned about (T10).",
    )
    overlap_region_alpha: float = Field(
        default=0.05,
        gt=0,
        lt=0.5,
        description="One-sided level of the Clopper-Pearson bound in the overlap-region check.",
    )
    overlap_region_depth: int = Field(
        default=4,
        ge=1,
        le=8,
        description="Depth of the tree whose leaves are the candidate regions: the most covariates "
        "a region can be defined by jointly.",
    )
    limited_overlap_bounds: tuple[float, float] = Field(
        default=(0.1, 0.9),
        description="Propensities outside these bounds (Crump et al. 2009) count toward the "
        "limited-overlap flag, set when their share exceeds max_extreme_propensity_share. The flag "
        "warns; it does not refuse or change the estimate.",
    )
    missing: Literal["error", "drop"] = "error"
    min_first_stage_f: float = Field(default=10.0, ge=0)
    cross_fit_folds: int = Field(default=5, ge=2)
    learners: Literal["linear", "flexible"] = "linear"
    refutation_alpha: float = Field(default=0.01, gt=0, lt=0.5)
    triangulation_alpha: float = Field(default=0.01, gt=0, lt=0.5)
    segment_alpha: float = Field(
        default=0.05,
        gt=0,
        lt=0.5,
        description="Level for the heterogeneity test across declared segments. Looser than "
        "the refutation alphas on purpose: this test decides whether segments may be "
        "compared at all, and missing real heterogeneity is the costlier error.",
    )
    implied_independence_tests: int = Field(default=30, ge=0)
    refuters: tuple[
        Literal["placebo_treatment", "negative_control_outcomes", "implied_independences"], ...
    ] = ("placebo_treatment", "negative_control_outcomes", "implied_independences")
    sensitivity: bool = True
    match_caliper: float = Field(
        default=0.2,
        gt=0,
        description="Rosenbaum bound: the matched design's caliper, in standard deviations of "
        "the propensity-score logit (Austin 2011). Units with no partner inside it are dropped.",
    )
    require_approved_graph: bool = False
    max_ci_width: float | None = Field(default=None, gt=0)
    pt_sensitivity_grid: tuple[float, ...] = Field(
        default=(0.0, 0.5, 1.0, 1.5, 2.0),
        description="Panel designs: multiples of the largest pre-adoption deviation at which to "
        "report parallel-trends-robust intervals.",
    )
    pt_alpha_pre: float = Field(default=0.025, gt=0, lt=0.5)
    pt_alpha_effect: float = Field(default=0.025, gt=0, lt=0.5)
    max_cluster_size_cv: float = Field(
        default=0.6,
        gt=0,
        description="Cluster-randomized designs: above this coefficient of variation of cluster "
        "sizes, a few large clusters dominate the estimate and its interval under-covers "
        "(T7 v2: 0.879 at CV 0.71); the analysis refuses.",
    )
    min_clusters_per_arm: int = Field(
        default=5,
        ge=2,
        description="Cluster-randomized designs: fewer clusters than this in either arm and the "
        "cluster-level variance cannot be estimated; the analysis refuses.",
    )
    carryover_alpha: float = Field(
        default=0.05,
        gt=0,
        lt=0.5,
        description="Switchback designs: level of the carryover check (the effect still moving "
        "after the washout, into- vs out-of-treatment switches). A failure makes the result "
        "contested. Looser than refutation_alpha on purpose: a missed carryover leaves a biased "
        "estimate supported (T8 v2).",
    )
    cs_target_n: int = Field(
        default=2000,
        ge=10,
        description="Sequential designs: the sample size the confidence sequence is tightest at.",
    )
    cs_look_every: int = Field(default=100, ge=1, description="Sequential designs: units per look.")
    cs_burn_in: int = Field(
        default=200,
        ge=20,
        description="Sequential designs: units before the first look. The sequence is asymptotic; "
        "below this it is not reported.",
    )
    seed: int = 0


# ── Identification ────────────────────────────────────────────────────────────


class ConditionCheck(_Model):
    name: str
    holds: bool
    detail: str = ""


class IdentificationCertificate(_Model):
    status: IdentificationStatus
    strategy: Strategy | None
    tier: Tier
    estimand: str = Field(description="The identifying functional, as a readable expression.")
    adjustment_set: tuple[str, ...] = ()
    instruments: tuple[str, ...] = ()
    mediators: tuple[str, ...] = ()
    treatment_proxies: tuple[str, ...] = ()
    outcome_proxies: tuple[str, ...] = ()
    conditions: tuple[ConditionCheck, ...] = ()
    assumptions: tuple[str, ...] = ()
    general_id: str | None = Field(
        default=None,
        description="Result of the complete ID algorithm on the same graph: the identifying "
        "expression, or 'FAIL: hedge ...'. Every strategy above must agree with it.",
    )
    unreviewed_edges: tuple[str, ...] = ()
    unreviewed_llm_edges: tuple[str, ...] = Field(
        default=(),
        description="The unreviewed edges a language model proposed (origin llm_prior, status "
        "proposed): a subset of unreviewed_edges. Structure that neither a person, data, an "
        "experiment nor the literature has vouched for.",
    )
    engine: str
    graph_digest: str


# ── Estimation ────────────────────────────────────────────────────────────────


class Interval(_Model):
    level: float
    lo: float
    hi: float
    method: str


class EstimateRecord(_Model):
    estimator: str
    family: str
    estimand: str
    point: float
    se: float | None
    ci: Interval | None
    ci_null_reason: str | None = None
    n_used: int
    assumptions: tuple[str, ...] = ()
    details: dict[str, float | str] = Field(default_factory=dict)


class PropensityDiagnostics(_Model):
    min: float
    max: float
    share_outside_bounds: float
    ess_treated: float
    ess_control: float


class OverlapRegion(_Model):
    """A region of the adjustment covariates in which one arm (almost) never occurs."""

    rule: str = Field(description="The region, as a conjunction of conditions on the adjustment "
                      "covariates as encoded (categorical ones as dummy columns).")
    n_units: int = Field(description="Held-out units in the region: the ones that judged it.")
    n_treated: int
    missing_arm: Literal["treated", "control"]
    upper_bound: float = Field(description="One-sided upper confidence bound on the probability "
                               "of the missing arm in this region, from the held-out units.")


class OverlapDiagnostics(_Model):
    """Where positivity fails or is thin beyond what one propensity model can express. The
    thresholds are the policy's (overlap_region_*, limited_overlap_bounds); the region check
    ran when ``diagnostics.checks`` has its row."""

    violation_share: float = Field(description="ATE: share of units in a violation region. ATT: "
                                   "share of treated units in a region without controls.")
    regions: tuple[OverlapRegion, ...] = Field(default=(), description="The violation regions, "
                                               "largest first (at most ten).")
    limited_share: float = Field(description="Share of units (ATT: treated units) whose "
                                         "propensity is outside limited_overlap_bounds (ATT: "
                                         "above the upper one).")
    limited_overlap: bool = Field(description="The share exceeds max_extreme_propensity_share: "
                                  "there the estimate rests on few comparable units or on the "
                                  "outcome model.")


class BalanceRow(_Model):
    covariate: str
    smd_raw: float
    smd_weighted: float | None


class DesignCheck(_Model):
    name: str
    passed: bool
    detail: str = ""


class Diagnostics(_Model):
    n_rows: int
    n_used: int
    n_dropped_missing: int = 0
    treatment_kind: Literal["binary", "continuous"]
    n_treated: int | None = None
    n_control: int | None = None
    outcome_sd: float | None = Field(
        default=None,
        description="Standard deviation of the outcome on the analysed rows. Reported because "
        "sizing a follow-up experiment needs it, and because an effect is only interpretable "
        "against the outcome's spread.",
    )
    propensity: PropensityDiagnostics | None = None
    overlap: OverlapDiagnostics | None = None
    balance: tuple[BalanceRow, ...] = ()
    srm_p_value: float | None = None
    n_clusters: int | None = None
    icc: float | None = Field(
        default=None,
        description="Cluster-randomized designs: estimated intra-cluster correlation of the "
        "outcome within arms. With clusters of size m, units carry about 1/(1 + (m-1)·ICC) of "
        "the information independent units would.",
    )
    first_stage_f: float | None = None
    checks: tuple[DesignCheck, ...] = ()


class RefutationResult(_Model):
    name: str
    passed: bool
    statistic: float | None = None
    p_value: float | None = None
    threshold: float | None = None
    detail: str = ""


class TrendSensitivityRow(_Model):
    m: float = Field(description="Post-adoption bias allowed, as a multiple of the largest "
                     "pre-adoption deviation.")
    lo: float
    hi: float


class ParallelTrendsSensitivity(_Model):
    """How far parallel trends may fail before the conclusion changes (panel designs)."""

    method: str
    alpha_pre: float
    alpha_effect: float
    pre_bound: float = Field(description="Upper confidence bound on the largest absolute "
                             "pre-adoption event-time effect.")
    rows: tuple[TrendSensitivityRow, ...]
    breakdown: float | None = Field(
        default=None,
        description="The largest multiple at which the interval still excludes zero. None when "
        "it includes zero even with no violation allowed.",
    )


class RosenbaumSensitivity(_Model):
    """How much hidden bias the matched comparison survives (back-door, binary treatment)."""

    method: str = Field(description="The matching and the test, in words.")
    n_pairs: int
    n_unmatched: int = Field(description="Units of the smaller arm with no partner inside the "
                             "caliper.")
    max_abs_smd: float | None = Field(
        default=None,
        description="Largest absolute standardized difference of the adjustment covariates "
        "between the matched arms. Residual imbalance is bias the bound does not cover.",
    )
    matched_difference: float = Field(
        description="Mean treated-minus-control difference over the pairs. Its population is "
        "the matched units, not the primary estimand's.",
    )
    gamma_star: float | None = Field(
        default=None,
        description="The sensitivity value: the largest gamma >= 1 at which the test of no "
        "effect, one-sided in the direction of the primary estimate at (1 - ci_level) / 2 (the "
        "level of the primary interval and of e_value_ci), still rejects; gamma is the factor "
        "by which two units with the same covariates may differ in their odds of treatment. "
        "None when the test does not reject even at gamma = 1.",
    )
    p_upper: dict[str, float] = Field(
        default_factory=dict,
        description="Upper bound on the one-sided p-value of no effect when hidden bias is at "
        "most gamma, keyed by gamma.",
    )


class Sensitivity(_Model):
    e_value: float | None = None
    e_value_ci: float | None = None
    e_value_method: str | None = None
    partial_r2_treatment: float | None = None
    robustness_value: float | None = None
    robustness_value_alpha: float | None = None
    parallel_trends: ParallelTrendsSensitivity | None = None
    rosenbaum: RosenbaumSensitivity | None = None
    rosenbaum_null_reason: str | None = None


class TriangulationPair(_Model):
    a: str
    b: str
    difference: float
    se_difference: float
    z: float


class Triangulation(_Model):
    estimators: tuple[str, ...]
    agree: bool
    pairs: tuple[TriangulationPair, ...] = ()
    detail: str = ""


class Claim(_Model):
    """A number that may appear in prose, bound to the field it came from.

    Narratives reference claims as ``⟦claim:<id>⟧``; the renderer substitutes
    ``text`` and the verifier rejects any number in the prose that is not a claim.
    """

    id: str
    value: float
    text: str
    unit: str | None = None
    path: str = Field(description="JSON pointer into the result that holds the value.")


class Provenance(_Model):
    contract_version: str
    core_version: str
    graph_digest: str | None = None
    query_digest: str | None = None
    data_digest: str | None = None
    policy_digest: str | None = None
    seed: int | None = None


# ── The three outcomes ────────────────────────────────────────────────────────


class SegmentEffect(_Model):
    """The effect within one declared subgroup, and what may be claimed about it."""

    variable: str
    level: str
    n_used: int
    estimate: EstimateRecord | None = Field(
        default=None, description="None when this segment could not be estimated."
    )
    family_wise_ci: Interval | None = Field(
        default=None,
        description="Interval at a level corrected for the number of segments examined. Any "
        "claim about this segment uses this, not the unadjusted one: having looked at k "
        "subgroups, the unadjusted interval no longer means what it says.",
    )
    p_value: float | None = None
    significant_after_correction: bool | None = Field(
        default=None,
        description="Holm's step-down decision across the family. None when the segment could "
        "not be estimated.",
    )
    refusal: AbstentionReason | None = Field(
        default=None, description="Why this segment has no estimate, when it has none."
    )
    refusal_detail: str = ""


class Heterogeneity(_Model):
    """Whether the segments differ at all, asked once, before any of them are compared."""

    method: Literal["cochran_q"] = "cochran_q"
    statistic: float
    df: int
    p_value: float
    alpha: float
    differs: bool = Field(
        description="False means the data cannot distinguish these segments from one another. "
        "Acting on the differences anyway is the error this test exists to prevent."
    )
    segments_compared: int


class EventTimeEffect(_Model):
    """The effect ``event_time`` periods after adoption (negative: before it).

    Pre-adoption event times are placebo estimates -- zero in expectation under parallel
    trends -- and period ``-1`` is the base, zero by construction, so it is not reported.
    """

    event_time: int
    estimate: EstimateRecord
    cohorts: int = Field(description="How many adoption cohorts contribute to this event time.")


class SequentialLook(_Model):
    n: int
    n_treated: int
    estimate: float
    lo: float
    hi: float


class SequentialSummary(_Model):
    """An anytime-valid reading of a randomized stream: valid at every look, jointly."""

    method: str
    alpha: float
    rho: float = Field(description="Mixture tuning, set from `cs_target_n`.")
    target_n: int
    looks: tuple[SequentialLook, ...]
    first_above_zero: int | None = Field(
        default=None, description="Units at the first look whose sequence lies wholly above zero."
    )
    first_below_zero: int | None = Field(
        default=None, description="Units at the first look whose sequence lies wholly below zero."
    )


class CausalResult(_Model):
    kind: Literal["result"] = "result"
    status: ResultStatus
    query: CausalQuery
    certificate: IdentificationCertificate
    primary: EstimateRecord
    estimates: tuple[EstimateRecord, ...]
    diagnostics: Diagnostics
    refutations: tuple[RefutationResult, ...] = ()
    sensitivity: Sensitivity | None = None
    triangulation: Triangulation | None = None
    segments: tuple[SegmentEffect, ...] = ()
    heterogeneity: Heterogeneity | None = None
    sequential: SequentialSummary | None = Field(
        default=None,
        description="Sequential designs only: the confidence sequence at every look. `primary` "
        "carries the last look, with the sequence as its interval.",
    )
    event_study: tuple[EventTimeEffect, ...] = Field(
        default=(),
        description="Panel designs only: the effect by periods since adoption, pre-periods "
        "included as placebos.",
    )
    claims: tuple[Claim, ...] = ()
    warnings: tuple[str, ...] = ()
    provenance: Provenance
    result_digest: str | None = Field(
        default=None, description="sha256 of the JCS form of this object without this field."
    )


class Abstention(_Model):
    kind: Literal["refusal"] = "refusal"
    reason: AbstentionReason
    detail: str
    missing_information: tuple[str, ...] = ()
    query: CausalQuery | None = None
    certificate: IdentificationCertificate | None = None
    diagnostics: Diagnostics | None = None
    provenance: Provenance


class ApparatusFailure(_Model):
    kind: Literal["apparatus_failure"] = "apparatus_failure"
    stage: str
    error_type: str
    detail: str
    provenance: Provenance


Outcome = Annotated[CausalResult | Abstention | ApparatusFailure, Field(discriminator="kind")]
OUTCOME_ADAPTER: TypeAdapter[CausalResult | Abstention | ApparatusFailure] = TypeAdapter(Outcome)

#: Every object published as a JSON Schema, keyed by schema file stem.
SCHEMA_MODELS: dict[str, type[BaseModel]] = {
    "causal_graph": CausalGraph,
    "causal_query": CausalQuery,
    "analysis_policy": AnalysisPolicy,
    "identification_certificate": IdentificationCertificate,
    "causal_result": CausalResult,
    "abstention": Abstention,
    "apparatus_failure": ApparatusFailure,
}


def json_schemas() -> dict[str, dict[str, object]]:
    """JSON Schema (2020-12) for every contract object, plus the outcome union."""
    out: dict[str, dict[str, object]] = {
        name: model.model_json_schema() for name, model in SCHEMA_MODELS.items()
    }
    out["outcome"] = OUTCOME_ADAPTER.json_schema()
    for name, schema in out.items():
        schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        schema["$id"] = f"urn:causalplatform:claims:{CONTRACT_VERSION}:{name}"
    return out
