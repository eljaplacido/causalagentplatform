"""
Seeded defects for T4. The gate must fail under every one.

Each patches one mechanism of the segment analysis in-process, so checks run
against them must be called with ``parallel=False`` -- a process pool would not
see the patch.

Two of these are patched on ``causal_core.pipeline`` rather than on
``causal_core.segments``, because the pipeline imported the function by name and
therefore holds its own reference: patching the definition leaves the caller
looking at the original. Both defects were silently absent for exactly that
reason, and the gate reported them as caught because an unrelated bar failed.
Checking *which* bar catches each defect is what surfaced it.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from typing import Any
from unittest import mock

import pandas as pd
from scipy import stats

import causal_core.pipeline as pipeline_mod
import causal_core.segments as seg_mod
from causal_core.contract import Heterogeneity, Interval, SegmentEffect


@contextmanager
def no_multiplicity_adjustment() -> Iterator[None]:
    """The family-wise interval is quietly the unadjusted one.

    The most valuable defect in this suite, because nothing about a single
    segment's output looks wrong: each interval is a perfectly good 95% interval
    for its own segment. Only the simultaneous coverage across the family reveals
    it, which is exactly why that bar exists.
    """

    real = seg_mod._family_wise_interval  # captured before patching, or this recurses

    def fake(point: float, se: float | None, level: float, k: int) -> Interval | None:
        return real(point, se, level, 1)

    with mock.patch.object(seg_mod, "_family_wise_interval", fake):
        yield


@contextmanager
def pooled_effect_per_segment() -> Iterator[None]:
    """Every segment reports the overall effect: segmentation in name only.

    The subgroup's rows are replaced by the whole dataset, so each "segment" is
    the pooled analysis wearing a label -- which is what a careless refactor of
    the subsetting would produce.
    """
    real_one = seg_mod._one_segment
    whole: dict[str, pd.DataFrame] = {}

    def fake_one(
        subset: pd.DataFrame,
        graph: Any,
        stratum_query: Any,
        policy: Any,
        analyzer: Any,
        variable: str,
        level: Any,
    ) -> SegmentEffect:
        return real_one(
            whole.get("frame", subset), graph, stratum_query, policy, analyzer, variable, level
        )

    def capture(
        frame: pd.DataFrame, graph: Any, query: Any, policy: Any, analyzer: Any
    ) -> tuple[tuple[SegmentEffect, ...], Heterogeneity | None, list[str]]:
        whole["frame"] = frame
        return seg_mod.estimate_segments(frame, graph, query, policy, analyzer)

    with (
        mock.patch.object(seg_mod, "_one_segment", fake_one),
        mock.patch.object(pipeline_mod, "estimate_segments", capture),
    ):
        yield


@contextmanager
def heterogeneity_anticonservative_df() -> Iterator[None]:
    """Cochran's Q compared against too few degrees of freedom.

    Registered as `heterogeneity_wrong_df`. Writing it showed the registered
    direction was the harmless one: comparing against chi-squared on k degrees of
    freedom instead of k-1 makes the test *more* conservative, which no bar in this
    suite can see. The direction that matters is the other one -- a test that
    invents heterogeneity that is not there -- so that is what is seeded. See the
    pre-registration's changelog.
    """

    real_q = seg_mod.cochran_q  # captured before patching, or this recurses

    def fake(
        points: Sequence[float], ses: Sequence[float], alpha: float
    ) -> Heterogeneity | None:
        real = real_q(points, ses, alpha)
        if real is None:
            return real
        df = max(1, real.df - 1)
        p = float(stats.chi2.sf(real.statistic, df))
        return real.model_copy(update={"df": df, "p_value": p, "differs": p < alpha})

    with mock.patch.object(seg_mod, "cochran_q", fake):
        yield


@contextmanager
def silent_segment_drop() -> Iterator[None]:
    """A segment that cannot be estimated is omitted instead of refused.

    The quietest way to mislead: the caller sees three healthy segments and no
    indication that a fourth existed and could not be answered.
    """
    def fake(
        frame: pd.DataFrame, graph: Any, query: Any, policy: Any, analyzer: Any
    ) -> tuple[tuple[SegmentEffect, ...], Heterogeneity | None, list[str]]:
        effects, het, warns = seg_mod.estimate_segments(frame, graph, query, policy, analyzer)
        return tuple(e for e in effects if e.estimate is not None), het, warns

    with mock.patch.object(pipeline_mod, "estimate_segments", fake):
        yield


DEFECTS: dict[str, Callable[[], Any]] = {
    "no_multiplicity_adjustment": no_multiplicity_adjustment,
    "pooled_effect_per_segment": pooled_effect_per_segment,
    "heterogeneity_anticonservative_df": heterogeneity_anticonservative_df,
    "silent_segment_drop": silent_segment_drop,
}
