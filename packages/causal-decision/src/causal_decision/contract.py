"""
The decision contract: what an agent must state before acting, and what it gets back.

The kernel (``causal-core``) answers *what the effect is, if it can be known*. This
layer answers a different question: *is that enough to act on?* The two are kept
apart on purpose. Evidence does not depend on who is asking or on how bad a mistake
would be; a decision depends on both.

Everything here is data. The verdict is a pure function of a request, a policy and a
causal outcome -- no clock, no network, no model -- so the same three inputs always
produce the same verdict, and a recorded decision can be replayed and disputed.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from causal_core.contract import Abstention, ApparatusFailure, CausalResult
from pydantic import BaseModel, ConfigDict, Field


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class RiskClass(StrEnum):
    """How much a wrong action costs. Set by the organisation, not by the evidence."""

    low = "low"  # small blast radius, cheaply undone
    medium = "medium"
    high = "high"  # wide blast radius, or expensive to undo


class Verdict(StrEnum):
    """Ordered from most to least autonomy. A check can only ever lower it."""

    act = "act"
    act_with_approval = "act_with_approval"
    experiment = "experiment"
    refuse = "refuse"


SEVERITY: dict[Verdict, int] = {
    Verdict.act: 0,
    Verdict.act_with_approval: 1,
    Verdict.experiment: 2,
    Verdict.refuse: 3,
}


class DecisionRequest(_Model):
    """What the agent proposes, in the terms a decision needs."""

    action: str = Field(description="The action in plain words, as it would be executed.")
    outcome: str = Field(description="The target outcome; must be the analysis outcome.")
    direction: Literal["increase", "decrease"] = Field(
        description="The desired direction of the outcome. Effects are read in this direction, "
        "so a 'benefit' is always positive below."
    )
    risk_class: RiskClass = RiskClass.medium
    reversible: bool = Field(
        default=True, description="Can the action be undone in practice, not in principle?"
    )
    minimum_worthwhile_effect: float = Field(
        default=0.0,
        ge=0.0,
        description="The smallest benefit, in outcome units, that justifies acting. Acting on an "
        "effect below it costs more than it returns, so the honest answer is to do nothing.",
    )
    harm_threshold: float = Field(
        default=0.0,
        ge=0.0,
        description="How much movement against the desired direction counts as harm, in outcome "
        "units. 0 means any adverse movement counts.",
    )
    population: str | None = None
    horizon: str | None = None
    can_randomize: bool = Field(
        default=True,
        description="Whether a bounded experiment on this action is possible at all. If not, an "
        "uncertain case becomes a human decision rather than an experiment.",
    )
    randomization_unit: str = Field(
        default="unit", description="What would be randomized: customer, ticket, service, site."
    )
    units_per_day: float | None = Field(
        default=None, gt=0, description="Arrival rate of those units, used to estimate duration."
    )


class RiskRule(_Model):
    """What a risk class demands of the evidence before an action may be automated."""

    allow_auto: bool
    require_identification_tier_a: bool
    require_refuters_passed: bool
    require_triangulation_agreement: bool
    min_e_value: float | None = Field(
        default=None,
        description="The E-value for the interval bound must exceed this: an unmeasured "
        "confounder of that strength would be needed to explain the effect away.",
    )
    max_ci_width_over_effect: float | None = Field(
        default=None,
        gt=0,
        description="Precision: the interval's width, divided by the absolute effect, must not "
        "exceed this. An effect known only to within several times itself is not actionable.",
    )


def _default_rules() -> dict[RiskClass, RiskRule]:
    return {
        RiskClass.low: RiskRule(
            allow_auto=True,
            require_identification_tier_a=False,
            require_refuters_passed=True,
            require_triangulation_agreement=True,
            min_e_value=1.25,
            max_ci_width_over_effect=2.0,
        ),
        RiskClass.medium: RiskRule(
            allow_auto=True,
            require_identification_tier_a=True,
            require_refuters_passed=True,
            require_triangulation_agreement=True,
            min_e_value=1.5,
            max_ci_width_over_effect=1.0,
        ),
        # High risk is never automated, whatever the evidence says. A human decides,
        # with the evidence in front of them.
        RiskClass.high: RiskRule(
            allow_auto=False,
            require_identification_tier_a=True,
            require_refuters_passed=True,
            require_triangulation_agreement=True,
            min_e_value=2.0,
            max_ci_width_over_effect=0.75,
        ),
    }


class DecisionPolicy(_Model):
    """The thresholds. Hashed into every decision, so a verdict names the policy it followed."""

    rules: dict[RiskClass, RiskRule] = Field(default_factory=_default_rules)
    plausible_confounding_rr: float = Field(
        default=1.5,
        ge=1.0,
        description="How strong unmeasured confounding is thought to be in this domain, as a risk "
        "ratio. An E-value below it means confounding of an ordinary size could explain the "
        "effect away. A domain assumption: state it, do not infer it from the data.",
    )
    experiments_available: bool = True
    power: float = Field(default=0.8, gt=0.5, lt=1)
    alpha: float = Field(default=0.05, gt=0, lt=0.5)


class Check(_Model):
    """One reason, with the verdict it allows. Checks never raise a verdict, only lower it."""

    name: str
    passed: bool
    allows: Verdict
    detail: str = ""


class Evidence(_Model):
    """The part of the causal outcome the verdict rested on, copied so a record stands alone."""

    kind: Literal["result", "refusal", "apparatus_failure"]
    status: str | None = None
    refusal_reason: str | None = None
    estimator: str | None = None
    estimand: str | None = None
    identification_strategy: str | None = None
    identification_tier: str | None = None
    benefit: float | None = Field(
        default=None, description="The effect read in the requested direction."
    )
    benefit_ci: tuple[float, float] | None = None
    ci_level: float | None = None
    e_value_ci: float | None = None
    refuters_failed: tuple[str, ...] = ()
    triangulation_agree: bool | None = None
    n_used: int | None = None
    outcome_sd: float | None = None
    result_digest: str | None = None


class ExperimentSpec(_Model):
    """The smallest experiment that would settle the question the evidence left open."""

    question: str
    design: Literal["randomized_units"]
    unit: str
    arms: tuple[str, str] = ("control", "treated")
    allocation: float = 0.5
    minimum_detectable_effect: float
    outcome_sd: float | None = None
    units_per_arm: int | None = None
    estimated_days: float | None = None
    guardrails: tuple[str, ...] = ()
    note: str = ""


class Decision(_Model):
    verdict: Verdict
    request: DecisionRequest
    evidence: Evidence
    checks: tuple[Check, ...]
    rationale: str
    experiment: ExperimentSpec | None = None
    what_would_change_it: tuple[str, ...] = ()
    policy_digest: str
    request_digest: str
    decision_digest: str | None = None


CausalOutcome = CausalResult | Abstention | ApparatusFailure
