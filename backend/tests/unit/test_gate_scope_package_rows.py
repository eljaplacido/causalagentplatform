"""gate_scope's package-row checks: package-files and gate-markers see a gate stop running.

The hole (audit, P1-2): a package row in ci.sh required only rc == 0 and tests
passed > 0. Renaming `def test_` to `def check_` in
packages/causal-core/tests/t5/test_t5_gate.py left the causal-core row PASS: the
T5 gate test silently stopped being collected and nothing counted it.

Negative controls: a JUnit report missing one test file fails package-files,
naming the file; one in which a gate marker has no PASSED test (none collected,
or all skipped or failed) fails gate-markers, naming the marker; a report made
without the markers plugin proves nothing and fails. The markers are required from
the skill's gate table and from the package's pyproject.toml, so neither source
can shrink the set alone.
"""

from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from types import ModuleType

    import pytest

REPO = Path(__file__).resolve().parents[3]
CORE = REPO / "packages" / "causal-core"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "gate_scope_rows_under_test", REPO / "scripts" / "gate_scope.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _classname(test_file: Path) -> str:
    return ".".join(test_file.relative_to(CORE).with_suffix("").parts)


def _junit(tmp_path: Path, cases: list[tuple[str, str, str]]) -> Path:
    """cases: (classname, markers, outcome) with outcome passed | skipped | failure."""
    rows = []
    for i, (cls, markers, outcome) in enumerate(cases):
        child = "" if outcome == "passed" else f'<{outcome} message="x"/>'
        rows.append(f'<testcase classname="{cls}" name="t{i}"><properties>'
                    f'<property name="markers" value="{markers}"/></properties>{child}</testcase>')
    path = tmp_path / "junit.xml"
    path.write_text('<?xml version="1.0"?><testsuites><testsuite name="pytest">'
                    + "".join(rows) + "</testsuite></testsuites>")
    return path


def _full_core_report() -> list[tuple[str, str, str]]:
    """One passing test per causal-core test file, each gate file carrying its marker."""
    cases = []
    for f in sorted((CORE / "tests").rglob("test_*.py")):
        rel = f.relative_to(CORE / "tests").parts
        # A gate's tests live in a directory named for its marker (t0, t4, ..., t10).
        marker = rel[0] if len(rel) > 1 and re.fullmatch(r"t\d+", rel[0]) else ""
        if f.name == "test_real_data.py":
            marker = "real_data"
        cases.append((_classname(f), marker, "passed"))
    return cases


def _gate(gs: ModuleType, junit: Path) -> bool:
    return bool(gs.check_gate_markers("causal-core", junit))


def test_the_required_markers_come_from_the_table_and_the_pyproject() -> None:
    gs = _load()
    table = gs.gate_table_markers()
    assert table["packages/causal-core"] >= {
        "t0", "real_data", "t4", "t5", "t6", "t7", "t8", "t9", "t10"
    }
    assert table["packages/causal-decision"] >= {"d0", "a0", "c0"}
    declared = gs.declared_gate_markers(CORE)
    assert {"t0", "t4", "t5", "t6", "t7", "t8", "t9", "t10", "real_data"} <= declared
    assert "slow" not in declared and "crosscheck" not in declared


def test_a_full_report_passes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    gs = _load()
    monkeypatch.setenv("CAUSAL_FETCH", "1")  # real_data due: its test must have passed
    junit = _junit(tmp_path, _full_core_report())
    expected, actual, _ = gs.check_package_files("causal-core", junit)
    assert expected == actual
    assert _gate(gs, junit)


def test_a_test_file_that_stopped_reporting_is_named(tmp_path: Path) -> None:
    gs = _load()
    t5 = _classname(CORE / "tests" / "t5" / "test_t5_gate.py")
    junit = _junit(tmp_path, [c for c in _full_core_report() if c[0] != t5])
    expected, actual, _ = gs.check_package_files("causal-core", junit)
    assert expected - actual == {"tests/t5/test_t5_gate.py"}


def test_a_gate_with_no_passing_test_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    gs = _load()
    monkeypatch.setenv("CAUSAL_FETCH", "1")
    full = _full_core_report()
    for broken in (
        [c for c in full if c[1] != "t5"],                               # never collected
        [(c[0], c[1], "skipped" if c[1] == "t5" else c[2]) for c in full],  # all skipped
        [(c[0], c[1], "failure" if c[1] == "t7" else c[2]) for c in full],  # all failed
    ):
        assert not _gate(gs, _junit(tmp_path, broken))
    out = capsys.readouterr().out
    assert "t5: 0 passed" in out and "t7: 0 passed" in out


def test_real_data_is_required_only_when_its_data_is_there(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gs = _load()
    no_real = [c for c in _full_core_report() if c[1] != "real_data"]
    monkeypatch.delenv("CAUSAL_FETCH", raising=False)
    monkeypatch.setenv("CAUSAL_CORE_CACHE", str(tmp_path / "empty-cache"))
    assert _gate(gs, _junit(tmp_path, no_real)), "not cached: not due"
    for sub in ("ihdp", "lalonde"):
        (tmp_path / "empty-cache" / sub).mkdir(parents=True)
        (tmp_path / "empty-cache" / sub / "f").write_text("x")
    assert not _gate(gs, _junit(tmp_path, no_real)), "cached: due, and nothing passed"


def test_a_report_without_the_markers_plugin_proves_nothing(tmp_path: Path) -> None:
    gs = _load()
    junit = tmp_path / "junit.xml"
    junit.write_text('<testsuites><testsuite><testcase classname="tests.test_x" name="t"/>'
                     "</testsuite></testsuites>")
    assert not _gate(gs, junit)


def test_the_plugin_writes_the_markers_into_a_real_report(tmp_path: Path) -> None:
    """End to end through pytest: the plugin ci.sh loads puts each test's markers in the
    JUnit report, where gate-markers reads them."""
    (tmp_path / "test_x.py").write_text(
        "import pytest\npytestmark = pytest.mark.t5\n\ndef test_a():\n    pass\n")
    junit = tmp_path / "j.xml"
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "junit_markers",
         "-o", "markers=t5", f"--junitxml={junit}", "-c", "/dev/null", str(tmp_path / "test_x.py")],
        cwd=tmp_path, capture_output=True, text=True, check=False,
        env={"PYTHONPATH": str(REPO / "scripts" / "ci_pytest_plugins"), "PATH": "/usr/bin:/bin"},
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    passed, _ = _load().passed_by_marker(junit)
    assert passed == {"t5": 1}
