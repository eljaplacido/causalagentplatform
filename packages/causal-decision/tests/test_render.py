"""
The explanation must be readable, and it must be true.

``explain`` renders the rationale's claim tokens against the analysis and then verifies
its own output. These tests pin both halves: that a person gets the verdict, the reasons
and the experiment, and that unverifiable prose is refused rather than printed.
"""

from __future__ import annotations

import pytest
from causal_core.contract import CausalResult

from causal_decision import Verdict, decide, explain
from causal_decision.bench.d0 import BASE, apparatus_failure, strong_effect, weak_effect


@pytest.fixture(scope="module")
def strong() -> CausalResult:
    out = strong_effect()
    assert isinstance(out, CausalResult)
    return out


def test_an_automated_decision_explains_itself_with_the_analysis_numbers(
    strong: CausalResult,
) -> None:
    d = decide(strong, BASE)
    text = explain(d, strong)
    assert text.startswith("ACT:")
    assert "Checks:" in text
    assert "⟦claim:" not in text, "a claim token was left unrendered"
    assert "minutes" in text
    # Every check it ran is shown, passed or failed: "what did you look at" is the answer.
    for c in d.checks:
        assert c.name in text


def test_an_open_question_prints_the_experiment_that_would_settle_it() -> None:
    out = weak_effect()
    d = decide(out, BASE)
    assert d.verdict is Verdict.experiment
    text = explain(d, out)
    assert "Experiment that would settle it:" in text
    assert "incident" in text and "Guardrail:" in text
    assert str(d.experiment.units_per_arm) in text  # type: ignore[union-attr]


def test_a_failed_analysis_explains_that_there_is_no_evidence_either_way() -> None:
    out = apparatus_failure()
    d = decide(out, BASE)
    text = explain(d, out)
    assert text.startswith("REFUSE:")
    assert "the analysis itself failed" in text.lower()
    assert "Do nothing on this evidence." in text


def test_prose_the_evidence_does_not_support_is_refused_not_printed(strong: CausalResult) -> None:
    d = decide(strong, BASE)
    tampered = d.model_copy(
        update={"rationale": d.rationale + " The effect is at least 42 minutes."}
    )
    with pytest.raises(ValueError, match="failed verification"):
        explain(tampered, strong)
    # Without strict it still renders, so a caller can inspect what was rejected.
    assert "42" in explain(tampered, strong, strict=False)


def test_the_thresholds_a_request_declared_are_allowed_but_invented_numbers_are_not(
    strong: CausalResult,
) -> None:
    d = decide(strong, BASE)
    lo, hi = d.evidence.benefit_ci  # type: ignore[misc]
    ok = d.rationale + f" Benefit {lo:.4g} to {hi:.4g}."
    explain(d.model_copy(update={"rationale": ok}), strong)
    with pytest.raises(ValueError, match="failed verification"):
        explain(d.model_copy(update={"rationale": d.rationale + " Benefit 999.9."}), strong)


def test_numbers_and_words_quoted_from_the_request_are_not_claims(strong: CausalResult) -> None:
    """The request's own text is quoted into the rationale ("Act: cut price 30%."), so a
    digit or a causal verb in it is the requester's, not the analysis's. It used to fail
    verification, and POST /api/v1/decisions answered 500 after sealing the entry."""
    request = BASE.model_copy(
        update={"action": "cut the on-call page threshold 30% because it causes toil"}
    )
    d = decide(strong, request)
    text = explain(d, strong)
    assert "cut the on-call page threshold 30% because it causes toil" in text
    # Quoting does not open a hole: a number outside the quote is still checked.
    tampered = d.model_copy(update={"rationale": d.rationale + " It saves 30 minutes."})
    with pytest.raises(ValueError, match="failed verification"):
        explain(tampered, strong)
