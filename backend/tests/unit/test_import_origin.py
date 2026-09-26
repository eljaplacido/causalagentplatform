"""The gate tests THIS tree's packages: scripts/import_origin.py, and ci.sh's wiring of it.

The hole (audit, P1-1): in a git worktree ci.sh borrows the main checkout's venvs,
whose editable installs of causal_core and causal_decision point at the main
checkout. The backend step set no PYTHONPATH and the package rows prepended only
their own src and causal-core's, so a `raise` planted in the worktree's
causal_core.pipeline.analyze still let the backend's effect-estimate and
decision-ledger tests pass.

Negative controls: a copy of a package placed ahead of this tree on sys.path --
which is what a borrowed editable install amounts to -- must make import_origin
FAIL and name the package; and every Python step in ci.sh must run under the
PYTHONPATH that puts every packages/*/src first, behind the import-origin step.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
SCRIPT = REPO / "scripts" / "import_origin.py"
CI_SH = REPO / ".gnomon" / "ci.sh"


def _src_path() -> str:
    return os.pathsep.join(str(p) for p in sorted((REPO / "packages").glob("*/src")) if p.is_dir())


def _run(pythonpath: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PYTHONPATH": pythonpath, "PYTHONDONTWRITEBYTECODE": "1"}
    return subprocess.run([sys.executable, str(SCRIPT), "--label", "test"], cwd=REPO, env=env,
                          capture_output=True, text=True, check=False)


def test_this_tree_first_passes() -> None:
    proc = _run(_src_path())
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "causal_core" in proc.stdout
    assert "causal_decision" in proc.stdout


def test_another_checkouts_copy_ahead_of_this_tree_fails(tmp_path: Path) -> None:
    shadow = tmp_path / "causal_decision"
    shadow.mkdir()
    (shadow / "__init__.py").write_text("")
    proc = _run(os.pathsep.join([str(tmp_path), _src_path()]))
    assert proc.returncode == 1, proc.stdout
    assert "causal_decision: would load from" in proc.stdout
    assert "causal_core:" not in proc.stdout, "only the shadowed package is named"


def test_behind_this_tree_the_copy_is_harmless(tmp_path: Path) -> None:
    (tmp_path / "causal_core").mkdir()
    (tmp_path / "causal_core" / "__init__.py").write_text("")
    assert _run(os.pathsep.join([_src_path(), str(tmp_path)])).returncode == 0


def test_every_python_step_in_ci_sh_imports_this_tree() -> None:
    """ci.sh exports every packages/*/src first, checks it with import-origin, and no
    pytest invocation replaces PYTHONPATH with a partial one."""
    src = CI_SH.read_text()
    assert re.search(r'for d in "\$ROOT"/packages/\*/src', src), "every packages/*/src"
    assert 'export PYTHONPATH="${PKG_SRC_PATH}' in src
    assert 'export MYPYPATH="${PKG_SRC_PATH}' in src
    assert re.search(r"^\s*record import-origin FAIL", src, re.M)
    assert src.index("record import-origin") < src.index("record backend-pytest ")
    joined = re.sub(r"\\\n\s*", " ", src)  # one logical line per backslash-continued command
    runs_pytest = re.compile(r'"\$PYBIN/pytest"|"\$\w+" -m pytest\b')
    pytest_lines = [ln for ln in joined.splitlines()
                    if runs_pytest.search(ln) and not ln.lstrip().startswith("#")]
    assert len(pytest_lines) >= 3, pytest_lines
    for line in pytest_lines:
        assert re.search(r'PYTHONPATH="\$(BACKEND|PKG)_PYTHONPATH"', line), (
            f"a pytest step without this tree's PYTHONPATH: {line.strip()}")
    for assignment in re.findall(r'^(?:BACKEND|PKG)_PYTHONPATH="([^"]*)"', src, re.M):
        assert assignment.endswith("$PYTHONPATH"), assignment
