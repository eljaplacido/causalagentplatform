"""
Seeded defects: plausible ways to write a decision procedure wrong.

The D0 gate must fail under every one of them. A gate that passes while the procedure
is broken certifies nothing, so these are not optional extras -- they are what gives the
gate's green the right to mean anything. Each defect patches one seam of
``causal_decision.policy`` in-process; ``tests/d0/test_seeded_defects.py`` runs each
against the check the pre-registration says must catch it.

The defects were chosen to be the mistakes a careful person actually makes: dropping a
guard that felt redundant, reading a ladder from the wrong end, or -- the dangerous one
here -- trusting an estimate the analysis had already flagged as contested.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any
from unittest import mock

from causal_core.contract import CausalResult, ResultStatus

import causal_decision.policy as policy_mod
from causal_decision.contract import (
    SEVERITY,
    Check,
    DecisionPolicy,
    DecisionRequest,
    Evidence,
    RiskRule,
    Verdict,
)

_WORTHWHILE = "the whole interval clears the minimum worthwhile effect"


@contextmanager
def ignore_reversibility() -> Iterator[None]:
    """The reversibility guard is dropped -- "the evidence is strong, just do it"."""
    with mock.patch.object(policy_mod, "_reversibility_check", lambda request: None):
        yield


@contextmanager
def ignore_risk_class() -> Iterator[None]:
    """A high-risk class no longer forces a person into the loop."""
    with mock.patch.object(policy_mod, "_risk_class_check", lambda request, rule: None):
        yield


@contextmanager
def best_check_wins() -> Iterator[None]:
    """The ladder is read from the wrong end: the most permissive failed check wins."""

    def fake(checks: list[Check]) -> Verdict:
        return min(
            (c.allows for c in checks if not c.passed),
            key=lambda v: SEVERITY[v],
            default=Verdict.act,
        )

    with mock.patch.object(policy_mod, "_weakest", fake):
        yield


@contextmanager
def ignore_worthwhile_effect() -> Iterator[None]:
    """Statistical significance stands in for worth: any effect above zero is acted on."""
    real = policy_mod._result_checks

    def fake(
        out: CausalResult,
        ev: Evidence,
        request: DecisionRequest,
        rule: RiskRule,
        policy: DecisionPolicy,
    ) -> list[Check]:
        return [c for c in real(out, ev, request, rule, policy) if c.name != _WORTHWHILE]

    with mock.patch.object(policy_mod, "_result_checks", fake):
        yield


@contextmanager
def refusal_becomes_approval() -> Iterator[None]:
    """A refusal is downgraded to something a person may wave through."""
    real_refusal, real_failure = policy_mod._refusal_check, policy_mod._failure_check

    def soften(check: Check) -> Check:
        return check.model_copy(update={"allows": Verdict.act_with_approval})

    with (
        mock.patch.object(
            policy_mod, "_refusal_check", lambda *a: soften(real_refusal(*a))
        ),
        mock.patch.object(
            policy_mod, "_failure_check", lambda *a: soften(real_failure(*a))
        ),
    ):
        yield


@contextmanager
def ignore_contested() -> Iterator[None]:
    """A contested result is read as if it were supported.

    The falsification tests still run and still fail; the layer simply does not let
    that reach the verdict. This is the failure mode the whole package exists to
    prevent, so the gate had better see it.
    """
    real = policy_mod._result_checks

    def fake(
        out: CausalResult,
        ev: Evidence,
        request: DecisionRequest,
        rule: RiskRule,
        policy: DecisionPolicy,
    ) -> list[Check]:
        clean_out = out.model_copy(
            update={
                "status": ResultStatus.supported,
                "refutations": tuple(
                    r.model_copy(update={"passed": True}) for r in out.refutations
                ),
                "triangulation": out.triangulation.model_copy(update={"agree": True})
                if out.triangulation
                else None,
            }
        )
        clean_ev = ev.model_copy(
            update={"status": "supported", "refuters_failed": (), "triangulation_agree": True}
        )
        return real(clean_out, clean_ev, request, rule, policy)

    with mock.patch.object(policy_mod, "_result_checks", fake):
        yield


@contextmanager
def ignore_segments() -> Iterator[None]:
    """Only the pooled effect is read -- "the average is good, ship it"."""
    with mock.patch.object(policy_mod, "_segment_checks", lambda ev, request, trustworthy: []):
        yield


DEFECTS: dict[str, Callable[[], Any]] = {
    "ignore_reversibility": ignore_reversibility,
    "ignore_risk_class": ignore_risk_class,
    "best_check_wins": best_check_wins,
    "ignore_worthwhile_effect": ignore_worthwhile_effect,
    "refusal_becomes_approval": refusal_becomes_approval,
    "ignore_contested": ignore_contested,
    "ignore_segments": ignore_segments,
}
