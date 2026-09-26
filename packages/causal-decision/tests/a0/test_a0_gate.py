"""
The A0 assumption-impact gate as tests. Bars come from
benchmarks/a0-assumption-impact/PRE-REGISTRATION.md; nothing is hard-coded here.

    pytest -m a0
"""

from __future__ import annotations

from typing import Any

import pytest

from causal_decision.bench import a0

pytestmark = pytest.mark.a0


@pytest.fixture(scope="module")
def cfg() -> dict[str, Any]:
    return a0.load_prereg()[0]


@pytest.fixture(scope="module")
def cases(cfg: dict[str, Any]) -> list[a0.Case]:
    c = cfg["cases"]
    return a0.generate(c["seed"], c["n_pairs"])[0]


def test_the_pre_registration_is_the_one_this_gate_runs(cfg: dict[str, Any]) -> None:
    assert cfg["meta"]["suite"] == "A0"
    assert cfg["agreement"]["must_match"] == 1.0


def test_impact_agrees_with_the_oracle_on_every_case(
    cfg: dict[str, Any], cases: list[a0.Case]
) -> None:
    got = a0.check_agreement(cases, cfg)
    assert got.n == cfg["cases"]["n_pairs"]
    assert got.unverified <= cfg["agreement"]["unverified_allowed"]
    assert got.matched / got.n >= cfg["agreement"]["must_match"], got.mismatches


def test_the_gate_looked_at_both_answers(cfg: dict[str, Any], cases: list[a0.Case]) -> None:
    """An impact() that always said `holds` must not be able to pass."""
    got = a0.check_agreement(cases, cfg)
    for cls in ("valid", "invalid"):
        assert got.by_oracle.get(cls, 0) >= cfg["cases"]["min_per_class"], got.by_oracle


def test_the_properties_hold(cfg: dict[str, Any], cases: list[a0.Case]) -> None:
    violations = a0.check_properties(cases)
    assert len(violations) <= cfg["properties"]["violations_allowed"], violations[:10]


def test_the_cases_depend_on_the_registered_seed_and_nothing_else() -> None:
    """v2 run 1's cases depended on PYTHONHASHSEED: a set was iterated unsorted."""
    import os
    import subprocess
    import sys

    probe = (
        "from causal_decision.bench import a0;"
        "cases, other = a0.generate(20260925, 60);"
        "print([(c.query.treatment, c.query.outcome, c.edit, c.new) for c in cases], "
        "sorted(other.items()))"
    )
    outs = {
        subprocess.run(
            [sys.executable, "-c", probe],
            # Only the hash seed varies. The rest of the environment is inherited:
            # a stripped one drops LD_LIBRARY_PATH, and a toolcache Python then loads
            # the host's older libpython (seen on a self-hosted runner: _ctypes
            # "undefined symbol _PyErr_SetLocaleString").
            env={**os.environ, "PYTHONHASHSEED": seed},
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        for seed in ("1", "2", "3")
    }
    assert len(outs) == 1, "the generated cases differ between processes"
