"""
scripts/skill_refs.py -- the check that the agent-facing instructions still point
at real things catches each kind of drift it claims to.

Preconditions: git on PATH (the checker lists files the way git would commit them).
Postconditions, from the script's docstring: it exits 0 on a tree whose references
all resolve, and exits 1 -- naming the file and the reference -- on a missing path,
a nonexistent script, an undeclared pytest marker, an unknown septacore
subcommand, a pre-registered benchmark the skill's gate table does not name, a
ci.sh step the skill does not name (or names but ci.sh does not record), a partial
list of gates, a stale allowlist entry, bad front matter, or a tree where nothing
was checked. Undefined here: the exact wording of the summary line beyond its
counts, and the order findings print in.

Negative controls: each test starts from one small tree that passes (asserted by
test_the_fixture_tree_passes, so a failure below is the planted defect and not
the fixture) and plants exactly one defect.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
SCRIPT = REPO / "scripts" / "skill_refs.py"

CONFIG = {
    "not_paths": [],
    "septacore": {"pinned_from": "test", "subcommands": ["check", "ready"]},
}

SKILL = """+++
name = "verifying changes"
description = "what decides pass/fail"
match = '\\b(verify|t0)\\b'
roles = ["implementor"]
+++

`.gnomon/ci.sh` decides. Its steps: `alpha-step` and `beta-step`.
Then run `python3 scripts/real.py` and `septacore ready`.

| Gate | Where | Marker | Pre-registration |
|---|---|---|---|
| T0 analytic | packages/core | `t0` | benchmarks/t0/ |

    cd packages/core && pytest -m t0
"""

AGENTS = """# AGENTS

The skills: `verifying-changes.md`. The gate is `.gnomon/ci.sh`; see
[the pre-registration](benchmarks/t0/PRE-REGISTRATION.md) and `scripts/real.py`.
"""

CI_SH = """#!/usr/bin/env bash
record alpha-step PASS "x" 0
ratchet_step beta-step ruff
"""

PYPROJECT = """[project]
name = "core"
version = "0"
[project.optional-dependencies]
dev = ["pytest>=8"]
[tool.pytest.ini_options]
markers = ["t0: the T0 gate"]
"""

BASE = {
    ".gnomon/skills/verifying-changes.md": SKILL,
    ".gnomon/ci.sh": CI_SH,
    ".gnomon/roles.toml": "[roles.implementor]\nmodel = \"m\"\n",
    "AGENTS.md": AGENTS,
    "benchmarks/t0/PRE-REGISTRATION.md": "# T0\n",
    "packages/core/pyproject.toml": PYPROJECT,
    "scripts/real.py": "print('hi')\n",
}


def _tree(tmp_path: Path, files: dict[str, str | None]) -> Path:
    """BASE with `files` laid over it (None deletes a file), in a git working tree."""
    root = tmp_path / "tree"
    root.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    merged: dict[str, str | None] = {**BASE, **files}
    for rel, text in merged.items():
        if text is None:
            continue
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return root


def _run(root: Path, config: dict[str, object] | None = None) -> subprocess.CompletedProcess[str]:
    cfg = root.parent / "skill-refs.json"
    cfg.write_text(json.dumps(config or CONFIG), encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root), "--config", str(cfg), "--septacore", ""],
        capture_output=True, text=True, check=False,
    )


def _fails_on(proc: subprocess.CompletedProcess[str], *needles: str) -> None:
    out = proc.stdout + proc.stderr
    assert proc.returncode == 1, out
    for n in needles:
        assert n in out, f"{n!r} not in:\n{out}"


def test_the_fixture_tree_passes(tmp_path: Path) -> None:
    proc = _run(_tree(tmp_path, {}))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "skill-refs:" in proc.stdout


def test_the_real_tree_passes() -> None:
    proc = subprocess.run(
        [sys.executable, str(SCRIPT)], capture_output=True, text=True, check=False
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    # Not vacuous: the real surfaces hold hundreds of references.
    n = int(proc.stdout.split("skill-refs: ", 1)[1].split(" ", 1)[0])
    assert n > 100, proc.stdout


def test_a_planted_missing_path_fails(tmp_path: Path) -> None:
    root = _tree(tmp_path, {"AGENTS.md": AGENTS + "\nAlso read `scripts/missing_gate.py`.\n"})
    _fails_on(_run(root), "AGENTS.md", "scripts/missing_gate.py", "no such file")


def test_a_missing_path_in_bare_prose_fails(tmp_path: Path) -> None:
    root = _tree(tmp_path, {"AGENTS.md": AGENTS + "\nSee benchmarks/t0/GONE.md for details.\n"})
    _fails_on(_run(root), "benchmarks/t0/GONE.md")


def test_a_broken_link_fails(tmp_path: Path) -> None:
    root = _tree(tmp_path, {"AGENTS.md": AGENTS + "\n[old](docs/RETIRED.md)\n"})
    _fails_on(_run(root), "link", "docs/RETIRED.md")


def test_a_nonexistent_script_fails(tmp_path: Path) -> None:
    skill = SKILL.replace("python3 scripts/real.py", "python3 scripts/renamed.py")
    root = _tree(tmp_path, {".gnomon/skills/verifying-changes.md": skill})
    _fails_on(_run(root), "script", "scripts/renamed.py")


def test_a_nonexistent_bash_script_fails(tmp_path: Path) -> None:
    root = _tree(tmp_path, {"AGENTS.md": AGENTS + "\nRun `bash .gnomon/old-gate.sh`.\n"})
    _fails_on(_run(root), "script", ".gnomon/old-gate.sh")


def test_an_unknown_marker_fails(tmp_path: Path) -> None:
    skill = SKILL.replace("pytest -m t0", 'pytest -m "t0 and not t9_retired"')
    root = _tree(tmp_path, {".gnomon/skills/verifying-changes.md": skill})
    _fails_on(_run(root), "marker", "t9_retired", "packages/core/pyproject.toml")


def test_an_unknown_marker_in_the_gate_table_fails(tmp_path: Path) -> None:
    skill = SKILL.replace("| `t0` |", "| `t0_gate` |")
    root = _tree(tmp_path, {".gnomon/skills/verifying-changes.md": skill})
    _fails_on(_run(root), "marker", "t0_gate")


def test_a_venv_tool_the_package_does_not_install_fails(tmp_path: Path) -> None:
    root = _tree(tmp_path, {"AGENTS.md": AGENTS + "\n    cd packages/core && .venv/bin/black .\n"})
    _fails_on(_run(root), "venv tool", "black")


def test_an_npm_script_the_package_does_not_declare_fails(tmp_path: Path) -> None:
    root = _tree(tmp_path, {
        "packages/core/package.json": '{"scripts": {"test": "vitest"}}',
        "AGENTS.md": AGENTS
        + "\n- `cd packages/core && npm run test`\n- `cd packages/core && npm run typecheck`\n",
    })
    _fails_on(_run(root), "npm run typecheck")
    assert "npm run test`" not in _run(root).stdout


def test_an_unknown_septacore_subcommand_fails(tmp_path: Path) -> None:
    root = _tree(tmp_path, {"AGENTS.md": AGENTS + "\nThen `septacore finish` it.\n"})
    _fails_on(_run(root), "septacore finish")


def test_a_benchmark_missing_from_the_gate_table_fails(tmp_path: Path) -> None:
    root = _tree(tmp_path, {"benchmarks/t9-new/PRE-REGISTRATION.md": "# T9\n"})
    _fails_on(_run(root), "benchmarks/t9-new/", "gate table does not name")


def test_a_gate_row_without_a_pre_registration_fails(tmp_path: Path) -> None:
    root = _tree(tmp_path, {"benchmarks/t0/PRE-REGISTRATION.md": None})
    _fails_on(_run(root), "benchmarks/t0/", "no PRE-REGISTRATION.md")


def test_a_partial_list_of_gates_fails(tmp_path: Path) -> None:
    skill = SKILL.replace(
        "| T0 analytic | packages/core | `t0` | benchmarks/t0/ |",
        "| T0 analytic | packages/core | `t0` | benchmarks/t0/ |\n"
        "| T2 real | packages/core | `t0` | benchmarks/t2/ |\n"
        "| T3 real | packages/core | `t0` | benchmarks/t3/ |\n"
        "| D0 decide | packages/core | `t0` | benchmarks/d0/ |",
    ).replace("match = '\\b(verify|t0)\\b'", "match = '\\b(verify|t0|t2|t3|d0)\\b'")
    files: dict[str, str | None] = {
        ".gnomon/skills/verifying-changes.md": skill,
        "benchmarks/t2/PRE-REGISTRATION.md": "#\n",
        "benchmarks/t3/PRE-REGISTRATION.md": "#\n",
        "benchmarks/d0/PRE-REGISTRATION.md": "#\n",
    }
    assert _run(_tree(tmp_path / "ok", files)).returncode == 0
    files["AGENTS.md"] = AGENTS + "\nThe gates (T0, T2, T3) run every PR.\n"
    _fails_on(_run(_tree(tmp_path / "bad", files)), "gate list", "leaves out D0")


def test_a_list_that_leaves_out_a_c_gate_fails(tmp_path: Path) -> None:
    """C0 (calibration) was the first gate whose id is not T, D or A; the parser missed it."""
    skill = SKILL.replace(
        "| T0 analytic | packages/core | `t0` | benchmarks/t0/ |",
        "| T0 analytic | packages/core | `t0` | benchmarks/t0/ |\n"
        "| T2 real | packages/core | `t0` | benchmarks/t2/ |\n"
        "| T3 real | packages/core | `t0` | benchmarks/t3/ |\n"
        "| C0 report | packages/core | `t0` | benchmarks/c0/ |",
    ).replace("match = '\\b(verify|t0)\\b'", "match = '\\b(verify|t0|t2|t3|c0)\\b'")
    files: dict[str, str | None] = {
        ".gnomon/skills/verifying-changes.md": skill,
        "benchmarks/t2/PRE-REGISTRATION.md": "#\n",
        "benchmarks/t3/PRE-REGISTRATION.md": "#\n",
        "benchmarks/c0/PRE-REGISTRATION.md": "#\n",
    }
    files["AGENTS.md"] = AGENTS + "\nThe gates (T0, T2, T3 and C0) run every PR.\n"
    assert _run(_tree(tmp_path / "ok", files)).returncode == 0
    files["AGENTS.md"] = AGENTS + "\nThe gates (T0, T2, T3) run every PR.\n"
    _fails_on(_run(_tree(tmp_path / "bad", files)), "gate list", "leaves out C0")


def test_a_skill_match_that_misses_a_gate_fails(tmp_path: Path) -> None:
    skill = SKILL.replace(
        "| T0 analytic | packages/core | `t0` | benchmarks/t0/ |",
        "| T0 analytic | packages/core | `t0` | benchmarks/t0/ |\n"
        "| T2 real | packages/core | `t0` | benchmarks/t2/ |",
    )
    root = _tree(tmp_path, {
        ".gnomon/skills/verifying-changes.md": skill,
        "benchmarks/t2/PRE-REGISTRATION.md": "#\n",
    })
    _fails_on(_run(root), "`match` fires on t0 but not on t2")


def test_a_ci_step_the_skill_does_not_name_fails(tmp_path: Path) -> None:
    root = _tree(tmp_path, {".gnomon/ci.sh": CI_SH + 'record gamma-step PASS "x" 0\n'})
    _fails_on(_run(root), "gamma-step", "never names")


def test_a_step_ci_sh_does_not_record_fails(tmp_path: Path) -> None:
    root = _tree(tmp_path, {"AGENTS.md": AGENTS + "\nThe `retired-step` step checks it.\n"})
    _fails_on(_run(root), "ci step", "retired-step")


def test_a_skill_missing_from_agents_md_fails(tmp_path: Path) -> None:
    front = SKILL.replace('name = "verifying changes"', 'name = "extra"').split("+++\n\n", 1)[0]
    extra = front + "+++\n\nbody\n"
    root = _tree(tmp_path, {".gnomon/skills/extra.md": extra})
    _fails_on(_run(root), "skill listed", "extra.md")


@pytest.mark.parametrize(
    ("front", "needle"),
    [
        ("name = \"x\"\ndescription = \"d\"\nmatch = '\\b(verify|t0)\\b'\nroles = [\n", "not TOML"),
        ("name = \"x\"\ndescription = \"d\"\n", "`match` missing"),
        ("description = \"d\"\nmatch = '\\b(verify|t0)\\b'\n", "`name` missing"),
        ("name = \"x\"\ndescription = \"d\"\nmatch = '(['\n", "does not compile"),
        (
            "name = \"x\"\ndescription = \"d\"\nmatch = '\\b(verify|t0)\\b'\nroles = [\"ghost\"]\n",
            "role `ghost`",
        ),
    ],
    ids=["not-toml", "no-match", "no-name", "match-does-not-compile", "unknown-role"],
)
def test_bad_front_matter_fails(tmp_path: Path, front: str, needle: str) -> None:
    body = SKILL.split("+++\n", 2)[2]
    root = _tree(tmp_path, {".gnomon/skills/verifying-changes.md": f"+++\n{front}+++\n{body}"})
    _fails_on(_run(root), "front matter", needle)


def test_a_skill_without_front_matter_fails(tmp_path: Path) -> None:
    body = SKILL.split("+++\n", 2)[2]
    root = _tree(tmp_path, {".gnomon/skills/verifying-changes.md": body})
    _fails_on(_run(root), "no +++ front matter")


def test_an_allowlisted_non_path_passes_and_a_stale_entry_fails(tmp_path: Path) -> None:
    root = _tree(tmp_path, {"AGENTS.md": AGENTS + "\nBranch from `feat/`.\n"})
    _fails_on(_run(root), "feat/")
    entry = {"ref": "feat/", "in": "AGENTS.md", "reason": "a branch prefix"}
    assert _run(root, {**CONFIG, "not_paths": [entry]}).returncode == 0
    stale = {"ref": "gone/", "in": "AGENTS.md", "reason": "was a branch prefix"}
    _fails_on(_run(root, {**CONFIG, "not_paths": [entry, stale]}), "gone/", "stale")


def test_an_allowlist_entry_without_a_reason_fails(tmp_path: Path) -> None:
    root = _tree(tmp_path, {"AGENTS.md": AGENTS + "\nBranch from `feat/`.\n"})
    _fails_on(_run(root, {**CONFIG, "not_paths": [{"ref": "feat/", "in": "AGENTS.md"}]}), "reason")


def test_placeholders_and_git_ignored_paths_are_not_findings(tmp_path: Path) -> None:
    root = _tree(tmp_path, {
        "AGENTS.md": AGENTS + "\nWrite `benchmarks/<gate>/PRE-REGISTRATION.md`;"
        " never `.env` or `packages/core/.coverage`.\n",
        ".gitignore": ".env\n.coverage\n",
    })
    proc = _run(root)
    assert proc.returncode == 0, proc.stdout
    assert "2 git-ignored" in proc.stdout and "1 placeholders" in proc.stdout, proc.stdout


def test_a_tree_with_nothing_to_check_fails(tmp_path: Path) -> None:
    root = tmp_path / "empty"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    (root / "README.md").write_text("nothing\n", encoding="utf-8")
    _fails_on(_run(root))
