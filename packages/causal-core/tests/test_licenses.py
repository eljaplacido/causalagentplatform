"""Every runtime dependency of causal-core is permissively licensed (scripts/check_licenses.py)."""

from __future__ import annotations

import runpy
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_licenses.py"


def test_runtime_dependencies_are_permissive() -> None:
    main = runpy.run_path(str(SCRIPT))["main"]
    assert main(["check_licenses.py", "causal-core"]) == 0
