"""Shared plumbing for the gate scripts in scripts/. Standard library only.

Every script here follows one rule, taken from gnomon (scripts/gate_scope.mjs):
**a gate that inspected nothing may not report success.** So each gate reports
how many things it looked at, and a count of zero is a failure, never a pass.

Nothing in this module decides a verdict. It finds the repository root, runs
git, and reads a committed baseline as it stood at a base ref -- the piece the
ratchets need to prove a committed number only ever moved in one direction.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent

RED = "\033[0;31m" if sys.stdout.isatty() else ""
GREEN = "\033[0;32m" if sys.stdout.isatty() else ""
YELLOW = "\033[0;33m" if sys.stdout.isatty() else ""
NC = "\033[0m" if sys.stdout.isatty() else ""


def ok(msg: str) -> None:
    print(f"{GREEN}ok{NC}   {msg}")


def warn(msg: str) -> None:
    print(f"{YELLOW}note{NC} {msg}")


def bad(msg: str) -> None:
    print(f"{RED}FAIL{NC} {msg}")


def git(*args: str, check: bool = True) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, check=False
    )
    if check and proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {proc.stderr.strip()[:200]}")
    return proc.stdout


def load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def dump_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def base_ref() -> str | None:
    """The ref a ratchet compares its committed baseline against.

    `CI_BASE_REF` wins (CI passes the PR base explicitly). Otherwise the first
    of origin/main, main that resolves. None means there is nothing to compare
    against -- a tarball, or a clone with no main -- and the caller must SAY so
    rather than treat it as a pass.
    """
    candidates = [os.environ.get("CI_BASE_REF", ""), "origin/main", "main"]
    for ref in candidates:
        if ref and git("rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}", check=False).strip():
            return ref
    return None


def baseline_at_base(rel_path: str) -> tuple[str, Any]:
    """Return (status, data) for a committed JSON file as it was at the merge base.

    status is one of:
      "no-base"   no base ref resolves; the monotonic check cannot run here
      "same"      the merge base IS this commit (e.g. a push build on main);
                  there is no earlier version to compare with
      "new"       the file does not exist at the merge base -- first commit of it
      "found"     data holds the parsed file at the merge base
    """
    ref = base_ref()
    if ref is None:
        return "no-base", None
    mb = git("merge-base", ref, "HEAD", check=False).strip()
    if not mb:
        return "no-base", None
    if mb == git("rev-parse", "HEAD").strip():
        return "same", None
    proc = subprocess.run(
        ["git", "show", f"{mb}:{rel_path}"], cwd=ROOT, capture_output=True, text=True, check=False
    )
    if proc.returncode != 0:
        return "new", None
    try:
        return "found", json.loads(proc.stdout)
    except json.JSONDecodeError:
        return "new", None


def ratchet_exempt_reason() -> str:
    """A declared reason for loosening a ratchet, or "" when none was declared.

    Read from RATCHET_EXEMPT (for a run made before the commit exists) or from a
    `Ratchet-Exempt: <why>` line in any commit message between the merge base
    and HEAD. A forcing function, not a lock -- anybody can write one. The point
    is that the reason lands in the history where a reviewer reads it.
    """
    env = os.environ.get("RATCHET_EXEMPT", "").strip()
    if env:
        return env
    ref = base_ref()
    if ref is None:
        return ""
    mb = git("merge-base", ref, "HEAD", check=False).strip()
    if not mb:
        return ""
    for line in git("log", "--format=%B", f"{mb}..HEAD", check=False).splitlines():
        if line.lower().startswith("ratchet-exempt:"):
            return line.split(":", 1)[1].strip()
    return ""


def describe_base_status(status: str, rel_path: str) -> str:
    return {
        "no-base": f"monotonic check NOT RUN for {rel_path}: no base ref (set CI_BASE_REF, or fetch main)",
        "same": f"monotonic check NOT RUN for {rel_path}: HEAD is the merge base (push build)",
        "new": f"monotonic check: {rel_path} is new since the merge base; nothing earlier to compare",
    }.get(status, "")
