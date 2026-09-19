"""The core stays a kernel: no web framework, LLM client, database driver or copyleft engine."""

from __future__ import annotations

import subprocess
import sys

FORBIDDEN = (
    "fastapi",
    "starlette",
    "uvicorn",
    "langchain",
    "langgraph",
    "openai",
    "anthropic",
    "sqlalchemy",
    "neo4j",
    "mcp",
    "tigramite",
    "dowhy",
    "y0",
    "sklearn",
)


def test_importing_the_core_pulls_in_no_forbidden_package() -> None:
    code = (
        "import sys, causal_core, causal_core.cli, causal_core.bench.t0; "
        f"print(','.join(m for m in {FORBIDDEN!r} if m in sys.modules))"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "", f"core imported: {out.stdout.strip()}"
