"""
Every seeded T4 defect must make its designated check fail.

The designated check per defect is the table in
benchmarks/t4-segments/PRE-REGISTRATION.md. The assertion is not merely "the gate
failed": each defect names the case whose bar must catch it, because a defect
"caught" by an unrelated failure is not caught at all -- two of these were in
exactly that state until the check below was written this way.
"""

from __future__ import annotations

from typing import Any

import pytest

from causal_core.bench import t4
from causal_core.bench.defects_t4 import DEFECTS

pytestmark = pytest.mark.t4


@pytest.fixture(scope="module")
def cfg() -> dict[str, Any]:
    return t4.load_prereg()[0]


#: defect -> (cases to run, a fragment the failure message must contain)
CATCHER: dict[str, tuple[tuple[int, ...], str]] = {
    "no_multiplicity_adjustment": ((3,), "simultaneous coverage"),
    "pooled_effect_per_segment": ((1,), "bias"),
    "heterogeneity_anticonservative_df": ((0,), "heterogeneity false positives"),
    "silent_segment_drop": ((5, 6), "must refuse every time"),
}


def test_every_registered_defect_has_a_catcher(cfg: dict[str, Any]) -> None:
    assert set(cfg["seeded_defects"]["required"]) == set(DEFECTS) == set(CATCHER)


@pytest.mark.parametrize("name", sorted(DEFECTS))
def test_defect_is_caught_by_its_designated_bar(cfg: dict[str, Any], name: str) -> None:
    case_ids, expected = CATCHER[name]
    cases = [t4.CASES[i] for i in case_ids]
    reps = cfg["seeded_defects"]["reps"]
    with DEFECTS[name]():
        # parallel=False: a process pool would not see the patch.
        _, failures = t4.check_estimation(cfg, reps=reps, parallel=False, cases=cases)
    assert failures, f"seeded defect {name!r} went undetected"
    assert any(expected in f for f in failures), (
        f"{name!r} failed the gate, but not on {expected!r}: {failures[:3]}"
    )
