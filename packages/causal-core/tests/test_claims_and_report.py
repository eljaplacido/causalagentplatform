"""Proof-carrying numbers and the causal-language policy."""

from __future__ import annotations

import pandas as pd
import pytest

import causal_core as cc
from causal_core.claims import ClaimError, format_number, render, verify
from causal_core.report import report, report_is_clean

from .conftest import CONFOUNDED


@pytest.fixture
def result(confounded_binary: pd.DataFrame) -> cc.CausalResult:
    out = cc.analyze(confounded_binary, CONFOUNDED, treatment="X", outcome="Y")
    assert isinstance(out, cc.CausalResult)
    return out


def test_render_substitutes_bound_values(result: cc.CausalResult) -> None:
    text = render("Effect ⟦claim:effect⟧ (⟦claim:ci.level⟧ CI [[claim:effect.ci.lo]]).", result)
    effect = next(c for c in result.claims if c.id == "effect")
    assert effect.text in text and "95%" in text and "⟦" not in text


def test_unknown_claim_token_raises(result: cc.CausalResult) -> None:
    with pytest.raises(ClaimError):
        render("⟦claim:does.not.exist⟧", result)


def test_verify_accepts_faithful_roundings_and_rejects_invented_numbers(
    result: cc.CausalResult,
) -> None:
    point = result.primary.point
    ok = verify(f"The effect is {point:.2f} (n = {result.diagnostics.n_used}).", result)
    assert ok.ok, ok.reasons()
    bad = verify(f"The effect is {point + 0.5:.2f} and 37% of users churn.", result)
    assert not bad.ok
    assert bad.unbound_numbers == [f"{point + 0.5:.2f}", "37"]


def test_zero_cannot_stand_in_for_a_small_nonzero_claim(result: cc.CausalResult) -> None:
    assert not verify("The standard error is 0.", result).ok


def test_causal_language_needs_a_supported_result(
    confounded_binary: pd.DataFrame, result: cc.CausalResult
) -> None:
    assert verify("X causes Y.", result).ok
    refusal = cc.analyze(confounded_binary, "X -> Y; X <-> Y", treatment="X", outcome="Y")
    v = verify("X causes Y and will reduce churn.", refusal)
    assert not v.ok and v.causal_language == ["causes", "will reduce"]
    # Describing the refusal itself is not a causal claim.
    assert verify("The effect is not identified from these data.", refusal).ok


def test_report_passes_its_own_verifier(result: cc.CausalResult) -> None:
    assert report_is_clean(result)
    assert "aipw" in report(result)


def test_refusal_report_lists_what_would_help(confounded_binary: pd.DataFrame) -> None:
    out = cc.analyze(confounded_binary, "X -> Y; X <-> Y", treatment="X", outcome="Y")
    text = report(out)
    assert text.startswith("No estimate: NON_IDENTIFIABLE") and "instrument" in text


@pytest.mark.parametrize(
    ("x", "s"),
    [(0.0, "0"), (3.0, "3"), (1.23456, "1.235"), (-0.000123456, "-0.0001235"), (1.5e-7, "1.5e-7")],
)
def test_format_number(x: float, s: str) -> None:
    assert format_number(x) == s
