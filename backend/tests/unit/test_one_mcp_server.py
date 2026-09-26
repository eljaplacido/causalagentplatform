"""One MCP server: packages/causal-mcp. The legacy backend server stays deleted.

backend/mcp_server (MCP SDK v1, seven backend skills, two of them heuristic triage
scores) shipped beside packages/causal-mcp until septa-6 deleted it (register entry
legacy-mcp-server, now in left_the_list). These assertions are what keeps it
deleted: a second server growing back in the backend fails here, not in review.
"""

from __future__ import annotations

import ast
import importlib.util
import re
import sys
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
BACKEND = REPO / "backend"


def test_the_legacy_package_is_gone() -> None:
    assert not (BACKEND / "mcp_server").exists()


def test_the_backend_ships_no_mcp_server() -> None:
    project = tomllib.loads((BACKEND / "pyproject.toml").read_text(encoding="utf-8"))
    assert "causal-platform-mcp" not in project["project"].get("scripts", {})
    deps = project["project"]["dependencies"]
    names = {re.split(r"[<>=!~\[; ]", d, maxsplit=1)[0].lower() for d in deps}
    assert "mcp" not in names, "the backend depends on the MCP SDK again"
    packages = project["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"]
    assert packages == ["src"]


def test_no_backend_module_imports_the_mcp_sdk() -> None:
    offenders = []
    for f in sorted((BACKEND / "src").rglob("*.py")):
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            mods = []
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                mods = [node.module]
            if any(m == "mcp" or m.startswith("mcp.") for m in mods):
                offenders.append(str(f.relative_to(REPO)))
    assert offenders == []


def test_gate_scope_inspects_the_one_server() -> None:
    spec = importlib.util.spec_from_file_location(
        "gate_scope_one_server", REPO / "scripts" / "gate_scope.py"
    )
    assert spec and spec.loader
    gs = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = gs
    spec.loader.exec_module(gs)
    assert not hasattr(gs, "check_mcp_tools")
    expected, actual, _ = gs.check_kernel_mcp_tools()
    assert expected and expected == actual
