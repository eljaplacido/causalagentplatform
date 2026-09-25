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

CONTRACT_VERSION = "0.8.0"


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
    contested   -- a falsification test failed or estimators disagree beyond
                   sampling noise. The numbers are reported; causal language is not.
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
    population: str | None = None
    horizon: str | None = None


class AnalysisPolicy(_Model):
    """Every threshold that can change an answer. Hashed into provenance."""

    ci_level: float = Field(default=0.95, gt=0.5, lt=1)
    min_n: int = Field(default=30, ge=2)
    min_per_arm: int = Field(default=10, ge=2)
    propensity_bounds: tuple[float, float] = (0.01, 0.99)
    max_extreme_propensity_share: float = Field(default=0.05, ge=0, le=1)
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
    require_approved_graph: bool = False
    max_ci_width: float | None = Field(default=None, gt=0)
    pt_sensitivity_grid: tuple[float, ...] = Field(
        default=(0.0, 0.5, 1.0, 1.5, 2.0),
        description="Panel designs: multiples of the largest pre-adoption deviation at which to "
        "report parallel-trends-robust intervals.",
    )
    pt_alpha_pre: float = Field(default=0.025, gt=0, lt=0.5)
    pt_alpha_effect: float = Field(default=0.025, gt=0, lt=0.5)
    min_clusters_per_arm: int = Field(
        default=5,
        ge=2,
        description="Cluster-randomized designs: fewer clusters than this in either arm and the "
        "cluster-level variance cannot be estimated; the analysis refuses.",
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


class Sensitivity(_Model):
    e_value: float | None = None
    e_value_ci: float | None = None
    e_value_method: str | None = None
    partial_r2_treatment: float | None = None
    robustness_value: float | None = None
    robustness_value_alpha: float | None = None
    parallel_trends: ParallelTrendsSensitivity | None = None


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
