"""Every pre-registered gate has a passing run on record against its current text.

The bench modules write `prereg_sha256` and `prereg_version` into every result,
and until this test nothing read them back. So a pre-registration could be edited
after its last run -- a bar moved, a scenario added -- and the committed results
would go on vouching for a document that no longer existed. D0 sat in exactly
that state from its v1.1 changelog edit until v2.

The rule: a PRE-REGISTRATION.md may change only together with a passing run
recorded against its exact bytes, from a committed (clean) tree. Failing runs stay on file beside it; they are
evidence too (T0 v1, T2 run 1).
"""

from __future__ import annotations

import hashlib
import json
import re
import tomllib
from pathlib import Path

import pytest


def _benchmarks() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "benchmarks").is_dir() and (parent / "packages").is_dir():
            return parent / "benchmarks"
    raise FileNotFoundError("run from the repository: benchmarks/ not found above this test")


GATES = sorted(p.parent.name for p in _benchmarks().glob("*/PRE-REGISTRATION.md"))


def test_the_gates_are_where_they_were() -> None:
    assert set(GATES) >= {"t0", "t2-ihdp", "t3-lalonde", "t4-segments", "d0-decision-policy"}


@pytest.mark.parametrize("gate", GATES)
def test_the_current_registration_has_a_passing_run(gate: str) -> None:
    root = _benchmarks() / gate
    raw = (root / "PRE-REGISTRATION.md").read_bytes()
    current = "sha256:" + hashlib.sha256(raw).hexdigest()
    block = re.search(r"```toml\n(.*?)```", raw.decode(), re.S)
    assert block, f"{gate}: pre-registration has no toml block"
    version = tomllib.loads(block.group(1))["meta"]["version"]

    runs = [json.loads(p.read_text()) for p in sorted((root / "results").glob("*.json"))]
    against_this_text = [r for r in runs if r.get("prereg_sha256") == current]
    assert against_this_text, (
        f"{gate}: no result was recorded against the current PRE-REGISTRATION.md "
        f"({current[:19]}...). It changed after its last run; run the gate and commit the result."
    )
    assert all(r.get("prereg_version") == version for r in against_this_text), (
        f"{gate}: a result for this text claims a different version than meta.version={version}"
    )
    latest = against_this_text[-1]
    assert latest.get("passed") is True, (
        f"{gate}: the latest run against the current registration did not pass"
    )
    # A number is only as good as the code that produced it. Fourteen results were once
    # recorded from uncommitted trees (git "...-dirty"); re-run on a clean commit, every one
    # reproduced byte for byte -- which is what this rule makes checkable rather than hoped.
    passing_clean = [
        r for r in against_this_text
        if r.get("passed") is True and not str(r.get("git", "")).endswith("-dirty")
    ]
    assert passing_clean, (
        f"{gate}: no passing run against the current registration was recorded from a clean "
        "tree; commit the code, then run the gate"
    )


def test_an_untracked_result_file_does_not_make_a_run_dirty() -> None:
    """Gates run in parallel write result files; those must not taint each other's provenance."""
    from causal_core.bench.t0 import _is_result_file

    assert _is_result_file("benchmarks/t2-ihdp/results/2026-09-25-v1-run3.json")
    assert not _is_result_file("packages/causal-core/src/causal_core/sequential.py")
    assert not _is_result_file("benchmarks/t5-did/PRE-REGISTRATION.md")
