#!/usr/bin/env python3
"""sbom_check -- a release SBOM parses and lists exactly the locked environment it claims to describe.

release.yml installs the built wheels into a fresh environment from the hash-pinned
lock requirements/<lock>.txt (scripts/py_lock.py), generates a CycloneDX SBOM from that
environment, and runs this before attaching it:

    scripts/sbom_check.py SBOM.json --lock release --package causal-core --package causal-decision ...

It holds that the file is CycloneDX JSON with a spec version; that every pin of the lock
whose marker holds on this interpreter is a component at the pinned version; that each
--package (the distributions the release publishes) is a component; and that nothing
else is -- an SBOM listing a package the lock does not pin describes some other
environment. Needs `packaging` (for the lock's markers).

Exit: 0 the SBOM matches; 1 it does not, or does not parse.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import py_lock  # noqa: E402  (a sibling script, not an installed module)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("sbom", help="a CycloneDX JSON file")
    ap.add_argument("--lock", required=True, choices=py_lock.LOCKS, help="the lock the environment was installed from")
    ap.add_argument("--package", action="append", default=[], help="a published distribution the SBOM must list")
    ap.add_argument("--root", default=str(py_lock.ROOT), help=argparse.SUPPRESS)
    args = ap.parse_args()
    problems: list[str] = []
    try:
        bom = json.loads(Path(args.sbom).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"FAIL {args.sbom} does not parse as JSON: {exc}")
        return 1
    if bom.get("bomFormat") != "CycloneDX" or not bom.get("specVersion"):
        problems.append(f"{args.sbom} is not a CycloneDX document (bomFormat {bom.get('bomFormat')!r})")
    listed: dict[str, str] = {}
    for comp in bom.get("components", []):
        listed[py_lock.norm(str(comp.get("name", "")))] = str(comp.get("version", ""))

    lock = py_lock.parse_lock(args.lock, Path(args.root))
    problems += lock.problems
    here = py_lock.probe(sys.executable)["markers"]
    pinned = py_lock.applicable(lock.pins, here)
    for name, version in sorted(pinned.items()):
        if name not in listed:
            problems.append(f"{name}=={version} is locked; the SBOM does not list it")
        elif listed[name] != version:
            problems.append(f"{name} is {listed[name]} in the SBOM, {version} in the lock")
    published = {py_lock.norm(p) for p in args.package}
    for name in sorted(published - listed.keys()):
        problems.append(f"the published package {name} is not in the SBOM")
    for name in sorted(listed.keys() - pinned.keys() - published):
        problems.append(f"{name}=={listed[name]} is in the SBOM but neither locked nor published")
    for p in problems:
        print(f"FAIL {p}")
    versions = ", ".join(f"{n} {listed.get(n, '?')}" for n in sorted(published))
    summary = (f"sbom: {len(listed)} components, CycloneDX {bom.get('specVersion')}; "
               f"{len(pinned)} locked pins ({args.lock}) "
               f"{'checked' if problems else 'listed at their versions'}; published: {versions}")
    print(("FAIL " if problems else "ok   ") + summary)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
