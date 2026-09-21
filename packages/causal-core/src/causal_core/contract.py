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

CONTRACT_VERSION = "0.3.0"


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
    implied_independence_tests: int = Field(default=30, ge=0)
    refuters: tuple[
        Literal["placebo_treatment", "negative_control_outcomes", "implied_independences"], ...
    ] = ("placebo_treatment", "negative_control_outcomes", "implied_independences")
    sensitivity: bool = True
    require_approved_graph: bool = False
    max_ci_width: float | None = Field(default=None, gt=0)
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
    first_stage_f: float | None = None
    checks: tuple[DesignCheck, ...] = ()


class RefutationResult(_Model):
    name: str
    passed: bool
    statistic: float | None = None
    p_value: float | None = None
    threshold: float | None = None
    detail: str = ""


class Sensitivity(_Model):
    e_value: float | None = None
    e_value_ci: float | None = None
    e_value_method: str | None = None
    partial_r2_treatment: float | None = None
    robustness_value: float | None = None
    robustness_value_alpha: float | None = None


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
