#!/usr/bin/env python3
"""contract_parity -- one vocabulary, three places to write it down.

The shared enumerations of this platform exist in three source files, because
three runtimes need them:

  py    backend/src/semantic_layer/models.py     StrEnum, the wire format
  pkg   packages/semantic-types/src/*.ts         the published TypeScript contract
  fe    frontend/src/lib/types.ts                the frontend's own declarations

Nothing made them agree. `tsc` cannot: a union redeclared in a second file is a
second type, not a conflict, so the frontend narrowing on "UNDER_REVIEW" against
a backend that only ever sends "PENDING" typechecks perfectly and matches
nothing at runtime. That is the bug this gate exists for, and it was real when
the gate was written: three names carried three different vocabularies.

WHAT IT CHECKS. For every name declared in two or more of the three sources,
the value sets must be identical. A name declared in only ONE source is
reported, never failed -- a frontend-only presentation enum (ThinkingStage,
Persona) is legitimate, and so is a backend-only one nothing has needed to
render yet. Only a name that two runtimes both claim to know can disagree.

KNOWN GAPS, the same discipline as scripts/gate_scope.py. Today's real
divergences are listed by name in scripts/contract-parity.json, so the gate
passes today and fails on any NEW one. The list may only shrink:

  * a divergence that has been fixed must be removed from the list (a stale
    entry is a FAIL), so closing it and recording it are one change;
  * the list growing since the merge base is a FAIL unless the commit carries
    `Ratchet-Exempt: <why>`.

A gate that inspected nothing may not report success, so this one reports how
many declarations it parsed from each source and fails if any source yields
zero -- a moved file or a changed declaration style would otherwise read green
while comparing nothing.

  scripts/contract_parity.py            report, exit 0 unless a NEW divergence
  scripts/contract_parity.py --list     print every parsed name and its sources
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _gatelib import (  # noqa: E402
    ROOT,
    bad,
    baseline_at_base,
    describe_base_status,
    load_json,
    ok,
    ratchet_exempt_reason,
    warn,
)

PARITY_FILE = "scripts/contract-parity.json"

PY_MODELS = ROOT / "backend/src/semantic_layer/models.py"
PKG_DIR = ROOT / "packages/semantic-types/src"
FE_TYPES = ROOT / "frontend/src/lib/types.ts"

#: TypeScript spells two of these differently from Python. The alias is
#: declared here rather than inferred, so a rename shows up as a new divergence
#: instead of quietly pairing the wrong two enums.
ALIASES = {"CausalEntityType": "EntityType"}


class ParityError(Exception):
    """A source could not be parsed, so the comparison proves nothing."""


def _rel(path: Path) -> str:
    """Repo-relative if it can be, absolute otherwise.

    The error paths below name the file that failed, and a file outside the
    repository is exactly the case where that name matters most -- so this must
    not be the thing that raises.
    """
    try:
        return str(Path(path).relative_to(ROOT))
    except ValueError:
        return str(path)


def py_enums(path: Path) -> dict[str, set[str]]:
    """Every `class X(StrEnum)` and its string values."""
    if not path.exists():
        raise ParityError(f"{_rel(path)} is missing")
    src = path.read_text()
    out: dict[str, set[str]] = {}
    for match in re.finditer(r"^class (\w+)\(StrEnum\):\n((?:[ \t]+.*\n|\s*\n)+)", src, re.M):
        values = set(re.findall(r'^\s+\w+ = "([^"]+)"', match.group(2), re.M))
        if values:
            out[match.group(1)] = values
    return out


def ts_unions(paths: list[Path]) -> dict[str, set[str]]:
    """Every `export type X = "a" | "b";` and its string members."""
    out: dict[str, set[str]] = {}
    for path in paths:
        if not path.exists():
            raise ParityError(f"{_rel(path)} is missing")
        src = path.read_text()
        for match in re.finditer(r'export type (\w+) =\s*((?:\s*\|?\s*"[^"]+")+)\s*;', src):
            out[match.group(1)] = set(re.findall(r'"([^"]+)"', match.group(2)))
    return out


def collect() -> dict[str, dict[str, set[str]]]:
    """name -> {source: values}, with the TypeScript aliases resolved."""
    py = py_enums(PY_MODELS)
    pkg = ts_unions(sorted(PKG_DIR.glob("*.ts")))
    fe = ts_unions([FE_TYPES])
    for label, parsed, where in (
        ("py", py, PY_MODELS),
        ("pkg", pkg, PKG_DIR),
        ("fe", fe, FE_TYPES),
    ):
        if not parsed:
            raise ParityError(
                f"parsed 0 declarations from {label} ({_rel(where)}) -- "
                "the file moved or its declaration style changed, so this gate is comparing nothing"
            )

    names: dict[str, dict[str, set[str]]] = {}
    for label, parsed in (("py", py), ("pkg", pkg), ("fe", fe)):
        for name, values in parsed.items():
            canonical = ALIASES.get(name, name)
            names.setdefault(canonical, {})[label] = values
    return names


def divergences(names: dict[str, dict[str, set[str]]]) -> dict[str, str]:
    """name -> one line saying how the sources disagree. Shared names only."""
    out: dict[str, str] = {}
    for name, by_source in sorted(names.items()):
        if len(by_source) < 2:
            continue
        distinct = {frozenset(v) for v in by_source.values()}
        if len(distinct) == 1:
            continue
        out[name] = "; ".join(
            f"{src}={sorted(vals)}" for src, vals in sorted(by_source.items())
        )
    return out


def base_parity() -> dict | None:
    status, old = baseline_at_base(PARITY_FILE)
    if status == "found":
        return old
    warn(describe_base_status(status, PARITY_FILE))
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description="contract_parity: one vocabulary, three sources")
    ap.add_argument("--list", action="store_true", help="print every parsed name and its sources")
    args = ap.parse_args()

    try:
        names = collect()
    except ParityError as exc:
        bad(f"contract-parity: {exc}")
        return 1

    if args.list:
        for name, by_source in sorted(names.items()):
            print(f"  {name:26} {'+'.join(sorted(by_source))}")
        print()

    found = divergences(names)
    known = load_json(ROOT / PARITY_FILE).get("known_divergences", {})
    shared = sum(1 for v in names.values() if len(v) > 1)

    new = sorted(set(found) - set(known))
    stale = sorted(set(known) - set(found))
    passed = True

    if new:
        bad(
            f"contract-parity: {len(new)} NEW divergence(s) -- the same name means "
            "different things in different runtimes:"
        )
        for name in new:
            print(f"       {name}: {found[name]}")
        passed = False

    if stale:
        bad(
            f"contract-parity: {len(stale)} listed divergence(s) no longer diverge -- "
            f"remove them from known_divergences in {PARITY_FILE} (the list only shrinks):"
        )
        for name in stale:
            print(f"       {name}")
        passed = False

    base = base_parity()
    if base is not None:
        grown = sorted(set(known) - set(base.get("known_divergences", {})))
        if grown:
            reason = ratchet_exempt_reason()
            if reason:
                warn(f"contract-parity: known_divergences GREW by {grown}; Ratchet-Exempt: {reason}")
            else:
                bad(
                    f"contract-parity: known_divergences grew since the merge base "
                    f"({', '.join(grown)}). Align the vocabularies instead, or say why with a "
                    "`Ratchet-Exempt:` commit line."
                )
                passed = False

    if passed:
        single = len(names) - shared
        extra = f", {len(found)} known divergence(s) listed" if found else ""
        ok(
            f"contract-parity: {shared - len(found)}/{shared} shared name(s) agree across "
            f"py/pkg/fe{extra}; {single} declared in one source only"
        )
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
