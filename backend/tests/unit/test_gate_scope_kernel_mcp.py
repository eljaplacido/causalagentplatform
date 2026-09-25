"""gate_scope's kernel-mcp-tools check: it sees today's tools, and it can fail.

The canonical MCP server (packages/causal-mcp) was, until this check, the one
surface whose tools no identity check inspected -- the old mcp-tools check reads
only the deprecated backend server. It is read statically because that server
needs MCP SDK v2 and gate_scope runs in the backend's environment.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from types import ModuleType

REPO = Path(__file__).resolve().parents[3]


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "gate_scope_under_test", REPO / "scripts" / "gate_scope.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_it_reads_every_tool_the_server_registers() -> None:
    gs = _load()
    tools = gs.kernel_mcp_tools()
    assert {"causal_analyze", "causal_decide", "causal_impact"} <= set(tools)
    assert tools["causal_impact"] == "causal_impact"
    expected, actual, _ = gs.check_kernel_mcp_tools()
    assert expected == actual


def test_an_untested_tool_is_a_gap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    gs = _load()
    (tmp_path / "src" / "causal_mcp").mkdir(parents=True)
    (tmp_path / "tests").mkdir()
    (tmp_path / "src" / "causal_mcp" / "server.py").write_text(
        'TOOLS: dict = {"causal_a": (tools.causal_a, "A")}\n'
        'TOOLS["causal_b"] = (tools.causal_b, "B")\n'
    )
    (tmp_path / "tests" / "test_x.py").write_text("def test_a():\n    causal_a()\n")
    monkeypatch.setattr(gs, "KERNEL_MCP", tmp_path)
    expected, actual, _ = gs.check_kernel_mcp_tools()
    assert expected == {"causal_a", "causal_b"}
    assert actual == {"causal_a"}, "causal_b is registered and no test references it"


def test_a_server_with_no_tools_table_proves_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gs = _load()
    (tmp_path / "src" / "causal_mcp").mkdir(parents=True)
    (tmp_path / "src" / "causal_mcp" / "server.py").write_text("x = 1\n")
    monkeypatch.setattr(gs, "KERNEL_MCP", tmp_path)
    with pytest.raises(gs.ScopeError):
        gs.check_kernel_mcp_tools()
