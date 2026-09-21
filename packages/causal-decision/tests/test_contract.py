"""The decision contract's own invariants: shape, ordering, and what a policy may say."""

from __future__ import annotations

import itertools

import pytest
from causal_core.canonical import digest
from pydantic import ValidationError

from causal_decision.contract import (
    SEVERITY,
    Check,
    DecisionPolicy,
    DecisionRequest,
    RiskClass,
    Verdict,
    _default_rules,
)


def test_every_verdict_is_ranked() -> None:
    assert set(SEVERITY) == set(Verdict)
    assert len(set(SEVERITY.values())) == len(Verdict), "two verdicts share a rank"
    assert SEVERITY[Verdict.act] < SEVERITY[Verdict.act_with_approval] < SEVERITY[
        Verdict.experiment
    ] < SEVERITY[Verdict.refuse]


def test_default_policy_covers_every_risk_class_and_never_automates_high_risk() -> None:
    rules = _default_rules()
    assert set(rules) == set(RiskClass)
    assert rules[RiskClass.high].allow_auto is False
    # Raising the risk class must never relax a bar, or the risk ladder is decorative.
    order = [RiskClass.low, RiskClass.medium, RiskClass.high]
    for lower, higher in itertools.pairwise(order):
        lo, hi = rules[lower], rules[higher]
        assert (hi.min_e_value or 0) >= (lo.min_e_value or 0)
        assert (hi.max_ci_width_over_effect or 0) <= (lo.max_ci_width_over_effect or 0)
        assert hi.require_identification_tier_a >= lo.require_identification_tier_a


def test_requests_are_frozen_and_reject_unknown_fields() -> None:
    req = DecisionRequest(action="a", outcome="y", direction="increase")
    with pytest.raises(ValidationError):
        req.action = "b"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        DecisionRequest(action="a", outcome="y", direction="increase", urgency="high")  # type: ignore[call-arg]


def test_a_worthwhile_effect_is_stated_in_benefit_terms_so_it_cannot_be_negative() -> None:
    with pytest.raises(ValidationError):
        DecisionRequest(
            action="a", outcome="y", direction="increase", minimum_worthwhile_effect=-1.0
        )


def test_policies_hash_by_value() -> None:
    a, b = DecisionPolicy(), DecisionPolicy()
    assert digest(a) == digest(b)
    assert digest(a) != digest(DecisionPolicy(plausible_confounding_rr=2.0))


def test_a_check_carries_the_strongest_verdict_it_allows() -> None:
    c = Check(name="n", passed=False, allows=Verdict.experiment, detail="d")
    assert c.allows is Verdict.experiment
    with pytest.raises(ValidationError):
        c.passed = True  # type: ignore[misc]
