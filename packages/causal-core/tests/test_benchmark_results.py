"""Every pre-registered gate has a passing run on record against its current text.

The bench modules write `prereg_sha256` and `prereg_version` into every result,
and until this test nothing read them back. So a pre-registration could be edited
after its last run -- a bar moved, a scenario added -- and the committed results
would go on vouching for a document that no longer existed. D0 sat in exactly
that state from its v1.1 changelog edit until v2.

The rule: a PRE-REGISTRATION.md may change only together with a passing run
recorded against its exact bytes, from a committed (clean) tree. Failing runs stay
on file beside it; they are evidence too (T0 v1, T2 run 1).

"A committed (clean) tree" is checked, not inferred: the run's `git` must be a full
40-hex commit, with no `-dirty`, that is an ancestor of HEAD. The earlier rule only
looked for a `-dirty` suffix, so `git: "unknown"` -- a run whose code nobody can
name -- counted as clean. t8-switchback/results/2026-09-26-v2-run1.json carries
exactly that; it stays on file (results are never rewritten) and no longer stands.

And a result's `passed` is not taken on its word: every verdict on file must agree
with the figures stored beside it (no failure recorded under a pass, every seeded
defect the registration requires recorded as caught). This does not re-judge each
numeric bar -- that is the gate's own code, and a second copy of it here would be a
second, unregistered judge -- only that the flag and the record do not contradict.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import tomllib
from pathlib import Path
from typing import Any

import pytest


def _benchmarks() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "benchmarks").is_dir() and (parent / "packages").is_dir():
            return parent / "benchmarks"
    raise FileNotFoundError("run from the repository: benchmarks/ not found above this test")


GATES = sorted(p.parent.name for p in _benchmarks().glob("*/PRE-REGISTRATION.md"))
SHA = re.compile(r"^[0-9a-f]{40}$")


def _traceable(git: object) -> bool:
    """The run names a committed tree this history contains: 40-hex, no -dirty, ancestor of HEAD."""
    sha = str(git or "")
    if not SHA.match(sha):
        return False
    proc = subprocess.run(
        ["git", "-C", str(_benchmarks()), "merge-base", "--is-ancestor", sha, "HEAD"],
        capture_output=True, check=False,
    )
    return proc.returncode == 0


def _clean_passes(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The runs that may stand: passed, and recorded from a traceable committed tree."""
    return [r for r in runs if r.get("passed") is True and _traceable(r.get("git"))]


def _registration(gate: str) -> tuple[str, dict[str, Any]]:
    raw = (_benchmarks() / gate / "PRE-REGISTRATION.md").read_bytes()
    block = re.search(r"```toml\n(.*?)```", raw.decode(), re.S)
    assert block, f"{gate}: pre-registration has no toml block"
    return "sha256:" + hashlib.sha256(raw).hexdigest(), tomllib.loads(block.group(1))


def _contradictions(node: Any, path: str = "") -> list[str]:
    """Figures in a result that say it did NOT pass: a non-empty `failures`, a set
    `first_failure`, a nested `passed: false`, a seeded defect not caught. The seeded
    defects' own designs are skipped: they are meant to fail."""
    found: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            here = f"{path}/{key}"
            if key == "seeded_defects_caught" and isinstance(value, dict):
                found += [f"{here}/{d} not caught" for d, c in value.items() if c is not True]
            elif key.startswith("seeded_defect"):
                continue
            elif key == "failures" and value:
                found.append(f"{here} is not empty")
            elif key == "first_failure" and value:
                found.append(f"{here} = {str(value)[:60]!r}")
            elif key == "passed" and value is False:
                found.append(f"{here} is false")
            else:
                found += _contradictions(value, here)
    elif isinstance(node, list):
        for i, value in enumerate(node):
            found += _contradictions(value, f"{path}[{i}]")
    return found


def test_the_gates_are_where_they_were() -> None:
    assert set(GATES) >= {"t0", "t2-ihdp", "t3-lalonde", "t4-segments", "d0-decision-policy"}


@pytest.mark.parametrize("gate", GATES)
def test_the_current_registration_has_a_passing_run(gate: str) -> None:
    root = _benchmarks() / gate
    current, cfg = _registration(gate)
    version = cfg["meta"]["version"]

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
    # `unknown`, a short sha or a commit outside this history is not a clean tree either.
    passing_clean = _clean_passes(against_this_text)
    assert passing_clean, (
        f"{gate}: no passing run against the current registration was recorded from a clean "
        "tree (a 40-hex commit, no -dirty, that is an ancestor of HEAD); commit the code, "
        "then run the gate"
    )


@pytest.mark.parametrize("gate", GATES)
def test_every_recorded_verdict_follows_from_its_figures(gate: str) -> None:
    """`passed` is re-derived, not trusted: a pass with a failure on file, or a pass that
    does not record catching every seeded defect the registration requires, is refused."""
    current, cfg = _registration(gate)
    required = set(cfg.get("seeded_defects", {}).get("required", []))
    for path in sorted((_benchmarks() / gate / "results").glob("*.json")):
        run = json.loads(path.read_text())
        against = _contradictions(run)
        caught = run.get("seeded_defects_caught")
        if run.get("prereg_sha256") == current and isinstance(caught, dict):
            against += [f"required seeded defect {d!r} not recorded"
                        for d in sorted(required - set(caught))]
        derived = not against
        assert run.get("passed") is derived, (
            f"{gate}/{path.name}: says passed={run.get('passed')!r}, but its figures say "
            f"{derived}: {against[:3]}"
        )


def test_a_verdict_contradicting_its_figures_is_caught() -> None:
    """Negative control for the re-derivation above."""
    assert _contradictions({"passed": True, "failures": [], "cases": [{"first_failure": ""}]}) == []
    assert _contradictions({"passed": True, "sections": {"x": {"failures": ["bias"]}}})
    assert _contradictions({"passed": True, "seeded_defects_caught": {"twfe_static": False}})
    assert _contradictions({"passed": True, "designs": [{"passed": False}]})
    # a seeded defect's own design is meant to fail, and is not a contradiction
    assert _contradictions({"seeded_defect_designs": {"d": {"first_failure": "x"}}}) == []


def test_unknown_or_untraceable_provenance_is_not_a_clean_tree() -> None:
    """Negative control: only a committed ancestor of HEAD counts as clean."""
    head = subprocess.run(["git", "-C", str(_benchmarks()), "rev-parse", "HEAD"],
                          capture_output=True, text=True, check=True).stdout.strip()
    assert _traceable(head)
    assert not _traceable("unknown")
    assert not _traceable(head + "-dirty")
    assert not _traceable(head[:12])
    assert not _traceable("0" * 40), "a well-formed sha this history does not contain"
    assert _clean_passes([{"passed": True, "git": "unknown"}]) == []
    assert _clean_passes([{"passed": True, "git": head + "-dirty"}]) == []
    assert _clean_passes([{"passed": True, "git": head}]) != []


def test_the_untraceable_t8_run_is_on_file_and_does_not_stand() -> None:
    """t8-switchback v2 run1 was recorded with git=unknown. It is kept (results are never
    rewritten), and the standing T8 run is a later, traceable one."""
    run = json.loads((_benchmarks() / "t8-switchback/results/2026-09-26-v2-run1.json").read_text())
    assert run["git"] == "unknown"
    assert not _traceable(run["git"])


def test_git_sha_names_the_code_not_the_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Negative control for bench provenance: run from inside ANOTHER repository (as a gate
    run from one worktree with a venv installed from another would be), the stamp must be
    the commit of the checkout causal_core was imported from, not the cwd's."""
    import causal_core.bench.t0 as t0

    def git(*args: str, cwd: Path) -> str:
        return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                              check=True).stdout.strip()

    other = tmp_path / "other"
    other.mkdir()
    git("init", "-q", cwd=other)
    (other / "f").write_text("x")
    git("add", "f", cwd=other)
    git("-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-qm", "x", cwd=other)
    other_sha = git("rev-parse", "HEAD", cwd=other)
    code_sha = git("rev-parse", "HEAD", cwd=Path(t0.__file__).parent)

    monkeypatch.chdir(other)
    stamped = t0.git_sha()
    assert stamped.removesuffix("-dirty") == code_sha
    assert other_sha != code_sha
    assert t0.git_sha(other / "f") == other_sha, "a gate in another package names its own code"


def test_an_untracked_result_file_does_not_make_a_run_dirty() -> None:
    """Gates run in parallel write result files; those must not taint each other's provenance."""
    from causal_core.bench.t0 import _is_result_file

    assert _is_result_file("benchmarks/t2-ihdp/results/2026-09-25-v1-run3.json")
    assert not _is_result_file("packages/causal-core/src/causal_core/sequential.py")
    assert not _is_result_file("benchmarks/t5-did/PRE-REGISTRATION.md")
