"""
The decision procedure, unit by unit.

The pre-registered gate (``pytest -m d0``) covers the table and the properties over a
sweep. These are the smaller statements: that the ladder only descends, that the verdict
is a pure function of its inputs, that an experiment is sized from the evidence, and that
a refusal never becomes an action.
"""

from __future__ import annotations

import causal_core as cc
import pytest
from causal_core.contract import (
    Abstention,
    AbstentionReason,
    ApparatusFailure,
    CausalResult,
    Provenance,
)

from causal_decision import DecisionPolicy, RiskClass, Verdict, decide
from causal_decision.bench.d0 import BASE, strong_effect, weak_effect
from causal_decision.policy import DEFAULT_MDE_IN_SD, _weakest


@pytest.fixture(scope="module")
def strong() -> CausalResult:
    out = strong_effect()
    assert isinstance(out, CausalResult)
    return out


def _prov() -> Provenance:
    return Provenance(contract_version=cc.CONTRACT_VERSION, core_version=cc.__version__)


def test_the_weakest_failed_check_decides(strong: CausalResult) -> None:
    from causal_decision.contract import Check

    checks = [
        Check(name="a", passed=True, allows=Verdict.act),
        Check(name="b", passed=False, allows=Verdict.act_with_approval),
        Check(name="c", passed=False, allows=Verdict.experiment),
        Check(name="d", passed=True, allows=Verdict.act),
    ]
    assert _weakest(checks) is Verdict.experiment
    assert _weakest([c for c in checks if c.passed]) is Verdict.act


def test_good_evidence_on_a_reversible_low_risk_action_is_automated(strong: CausalResult) -> None:
    d = decide(strong, BASE.model_copy(update={"risk_class": RiskClass.low}))
    assert d.verdict is Verdict.act
    assert d.experiment is None
    assert d.what_would_change_it == ()
    assert all(c.passed for c in d.checks)


def test_the_decision_records_what_it_looked_at_even_when_everything_passed(
    strong: CausalResult,
) -> None:
    d = decide(strong, BASE)
    names = {c.name for c in d.checks}
    assert "the whole interval clears the minimum worthwhile effect" in names
    assert "falsification tests passed" in names
    assert "robust to unmeasured confounding" in names
    assert len(names) == len(d.checks), "a check was recorded twice"


def test_direction_is_read_as_benefit_in_both_directions(strong: CausalResult) -> None:
    down = decide(strong, BASE).evidence
    up = decide(strong, BASE.model_copy(update={"direction": "increase"})).evidence
    assert down.benefit is not None and up.benefit is not None
    assert down.benefit == pytest.approx(-up.benefit)
    assert down.benefit > 0, "the playbook shortens incidents, so shortening them is the benefit"
    assert down.benefit_ci is not None and down.benefit_ci[0] < down.benefit_ci[1]


def test_wanting_the_wrong_direction_is_not_an_action(strong: CausalResult) -> None:
    """Asking to *increase* minutes with an effect that decreases them must not act."""
    d = decide(strong, BASE.model_copy(update={"direction": "increase"}))
    assert d.verdict in (Verdict.experiment, Verdict.refuse)


def test_an_irreversible_action_is_never_automated(strong: CausalResult) -> None:
    d = decide(strong, BASE.model_copy(update={"reversible": False, "risk_class": RiskClass.low}))
    assert d.verdict is Verdict.act_with_approval
    assert any("reversible" in c.name for c in d.checks if not c.passed)


def test_a_refusal_no_experiment_can_resolve_ends_in_refuse() -> None:
    out = Abstention(
        reason=AbstentionReason.UNAPPROVED_ASSUMPTION, detail="unreviewed edges", provenance=_prov()
    )
    d = decide(out, BASE)
    assert d.verdict is Verdict.refuse
    assert d.evidence.kind == "refusal"
    assert d.experiment is None


def test_an_apparatus_failure_is_never_evidence() -> None:
    out = ApparatusFailure(
        stage="estimate", error_type="NumericalInstabilityError", detail="x", provenance=_prov()
    )
    d = decide(out, BASE)
    assert d.verdict is Verdict.refuse
    assert d.evidence.kind == "apparatus_failure"
    assert d.evidence.benefit is None


def test_an_open_question_with_no_experiment_available_goes_to_a_person() -> None:
    out = weak_effect()
    assert decide(out, BASE).verdict is Verdict.experiment
    d = decide(out, BASE, DecisionPolicy(experiments_available=False))
    assert d.verdict is Verdict.act_with_approval
    assert any(c.name == "an experiment is possible" for c in d.checks)
    assert decide(out, BASE.model_copy(update={"can_randomize": False})).verdict is (
        Verdict.act_with_approval
    )


def test_the_experiment_is_sized_from_the_evidence_and_the_bar() -> None:
    d = decide(weak_effect(), BASE)
    spec = d.experiment
    assert spec is not None
    assert spec.minimum_detectable_effect == BASE.minimum_worthwhile_effect
    assert spec.units_per_arm and spec.units_per_arm > 0
    assert spec.estimated_days == pytest.approx(
        round(2 * spec.units_per_arm / BASE.units_per_day, 1)  # type: ignore[operator]
    )
    assert any("randomize" in g for g in spec.guardrails)


def test_a_smaller_detectable_effect_costs_more_units() -> None:
    fine = decide(weak_effect(), BASE.model_copy(update={"minimum_worthwhile_effect": 2.0}))
    coarse = decide(weak_effect(), BASE)
    assert fine.experiment and coarse.experiment
    assert fine.experiment.units_per_arm > coarse.experiment.units_per_arm  # type: ignore[operator]


def test_with_no_bar_stated_the_experiment_says_what_convention_it_fell_back_on() -> None:
    out = weak_effect()
    d = decide(
        out, BASE.model_copy(update={"minimum_worthwhile_effect": 0.0, "direction": "increase"})
    )
    if d.experiment is not None and d.evidence.benefit_ci is not None:
        assert d.experiment.minimum_detectable_effect > 0
        assert d.experiment.note, "a fallback size must say it is a fallback"
        assert str(DEFAULT_MDE_IN_SD) in d.experiment.note or "observational" in d.experiment.note


def test_the_same_inputs_give_the_same_decision_and_a_different_policy_does_not(
    strong: CausalResult,
) -> None:
    a = decide(strong, BASE)
    b = decide(strong, BASE)
    assert a.decision_digest == b.decision_digest
    assert a.policy_digest == b.policy_digest and a.request_digest == b.request_digest
    c = decide(strong, BASE, DecisionPolicy(plausible_confounding_rr=3.0))
    assert c.policy_digest != a.policy_digest
    assert c.decision_digest != a.decision_digest, "the decision must name the policy it followed"


def test_the_evidence_record_stands_alone(strong: CausalResult) -> None:
    d = decide(strong, BASE)
    ev = d.evidence
    assert ev.result_digest and ev.estimator == "aipw"
    assert ev.estimand and ev.estimand.startswith("ATT:"), ev.estimand
    assert ev.identification_tier and ev.n_used and ev.outcome_sd
    assert ev.ci_level == pytest.approx(0.95)
