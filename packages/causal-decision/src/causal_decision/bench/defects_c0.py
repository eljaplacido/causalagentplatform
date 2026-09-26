"""
Seeded defects for C0: plausible ways to write a calibration report that misleads.

Each is something a reasonable first version does -- a textbook Wald interval, a
filter that keeps only the follow-ups with an answer, a rate from whatever count
there is, "not significant" read as "no difference". Each patches one seam of
``causal_decision.outcome`` in-process; ``tests/c0/test_seeded_defects.py`` runs the
gate under each and asserts the checks the pre-registration names all fail.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from typing import Any
from unittest import mock

import causal_decision.outcome as oc

_real_report = oc.report
_real_paired = oc.paired_comparison


@contextmanager
def wald_interval() -> Iterator[None]:
    """p-hat +/- z * sqrt(p-hat (1 - p-hat) / n): [1, 1] at 15 of 15."""

    def wald(successes: int, n: int, level: float) -> tuple[float, float]:
        from scipy.stats import norm

        p = successes / n
        h = float(norm.ppf(1 - (1 - level) / 2)) * math.sqrt(p * (1 - p) / n)
        return max(0.0, p - h), min(1.0, p + h)

    with mock.patch.object(oc, "_interval", wald):
        yield


@contextmanager
def undecidable_as_agreement() -> Iterator[None]:
    """An undecidable follow-up is scored as a decidable one on which everything agreed."""

    def report(realizations: list[oc.Realization], **kw: Any) -> oc.CalibrationReport:
        agreeing = [
            r if r.decidable else r.model_copy(update={
                "decidable": True, "undecidable_reason": None, "point_in_predicted_ci": True,
                "intervals_overlap": True, "cleared_worthwhile": True, "realized_harm": False,
            })
            for r in realizations
        ]
        return _real_report(agreeing, **kw)

    with mock.patch.object(oc, "report", report):
        yield


@contextmanager
def drop_undecidable() -> Iterator[None]:
    """Only follow-ups with an answer reach the report -- the most flattering subset, or not."""

    def report(realizations: list[oc.Realization], **kw: Any) -> oc.CalibrationReport:
        return _real_report([r for r in realizations if r.decidable], **kw)

    with mock.patch.object(oc, "report", report):
        yield


@contextmanager
def no_minimum() -> Iterator[None]:
    """A rate from any count at all: one follow-up reads as 100%."""
    with mock.patch.object(oc, "_refused", lambda *a, **k: None):
        yield


@contextmanager
def unresolved_reads_as_no_difference() -> Iterator[None]:
    """Too few discordant pairs to ever reject, reported as 'no difference detected'."""

    def paired(b: int, c: int, n_pairs: int, **kw: Any) -> oc.PairedComparison:
        got = _real_paired(b, c, n_pairs, **kw)
        if got.status == "could_not_resolve" and b + c < got.min_discordant:
            return got.model_copy(update={"status": "no_difference_detected"})
        return got

    with mock.patch.object(oc, "paired_comparison", paired):
        yield


DEFECTS: dict[str, Callable[[], AbstractContextManager[None]]] = {
    "wald_interval": wald_interval,
    "undecidable_as_agreement": undecidable_as_agreement,
    "drop_undecidable": drop_undecidable,
    "no_minimum": no_minimum,
    "unresolved_reads_as_no_difference": unresolved_reads_as_no_difference,
}
