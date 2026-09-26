#!/usr/bin/env python3
"""import_origin -- the gate tests the code in THIS tree, not a copy it happens to import.

Run it with the interpreter a gate step will use, under the environment that step
gets (ci.sh exports PYTHONPATH before it runs anything). For every Python package
under packages/*/src/ it asks that interpreter where `import <package>` would load
from -- importlib.util.find_spec, which consults the same finders an import does
without executing the package -- and fails unless the answer is inside this tree's
packages/<name>/src/.

Why this exists: in a git worktree the gate borrows the main checkout's virtualenvs,
and their editable installs of causal_core and causal_decision point at the MAIN
checkout. With nothing ahead of them on sys.path, the backend suite and the package
suites imported that checkout's kernel: a `raise RuntimeError` planted at the top of
causal_core.pipeline.analyze in the tree under test still let the backend's
decision-ledger and effect-estimate tests pass. The fix is PYTHONPATH (ci.sh); this
is the check that the fix is in force, per interpreter.

Usage:
  <python> scripts/import_origin.py [--label NAME]
Exit 0 every package resolves inside this tree; 1 one does not (or none was found).
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def packages() -> list[tuple[str, Path]]:
    """(module name, the src/ directory it must load from) for every packages/*/src/<mod>/."""
    found = []
    for init in sorted(ROOT.glob("packages/*/src/*/__init__.py")):
        found.append((init.parent.name, init.parent.parent))
    return found


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--label", default="", help="which step's interpreter this is, for the report")
    args = ap.parse_args()
    label = f" [{args.label}]" if args.label else ""

    mods = packages()
    if not mods:
        print(f"FAIL import-origin{label}: found no packages/*/src/<module>/__init__.py -- nothing to check")
        return 1
    bad = []
    for mod, src in mods:
        try:
            spec = importlib.util.find_spec(mod)
        except (ImportError, ValueError) as exc:  # a broken finder is a failure, not a pass
            bad.append(f"{mod}: find_spec raised {exc!r}")
            continue
        origin = Path(spec.origin).resolve() if spec and spec.origin else None
        if origin is None:
            bad.append(f"{mod}: not importable by {sys.executable}")
        elif not origin.is_relative_to(src.resolve()):
            bad.append(f"{mod}: would load from {origin}, not from {src.relative_to(ROOT)}/")
    if bad:
        print(f"FAIL import-origin{label}: {len(bad)} of {len(mods)} package(s) resolve outside this tree "
              "-- the tests would run another checkout's code (is PYTHONPATH set?)")
        for line in bad:
            print(f"       {line}")
        return 1
    names = ", ".join(m for m, _ in mods)
    print(f"ok   import-origin{label}: {len(mods)} packages ({names}) load from this tree")
    return 0


if __name__ == "__main__":
    sys.exit(main())
