"""
Licence gate: every runtime dependency of a package, resolved transitively from
installed metadata, must be under a permissive licence.

    python scripts/check_licenses.py causal-core
    python scripts/check_licenses.py causal-mcp

Extras (dev, crosscheck, ml, http) are not runtime dependencies and are not
checked. Copyleft engines may only ever run as out-of-process plug-ins.
Exit status 1 lists every offending or unclassifiable distribution; a gate
that inspected nothing exits 1 too.
"""

from __future__ import annotations

import re
import sys
from importlib.metadata import PackageNotFoundError, distribution

from packaging.requirements import Requirement

PERMISSIVE = re.compile(
    r"\b(MIT|BSD|Apache|PSF|Python Software Foundation|ISC|Unlicense|Zlib|0BSD|CC0|"
    r"Historical Permission Notice|HPND)\b",
    re.I,
)
COPYLEFT = re.compile(r"\b(GPL|AGPL|LGPL|SSPL|BUSL|Business Source|EUPL|CC-BY-NC|CC-BY-SA)\b", re.I)
# Distributions classified by hand after reading their licence file, with the reason.
# Empty on purpose: add an entry only for a dependency that is actually present.
KNOWN: dict[str, str] = {}


def _name(req: str) -> str | None:
    """The distribution a requirement names, or None if it does not apply here.

    Extras are not runtime dependencies; platform markers (e.g. Windows-only
    tzdata / pywin32) are evaluated for the platform the gate runs on.
    """
    parsed = Requirement(req)
    if parsed.marker is not None and not parsed.marker.evaluate({"extra": ""}):
        return None
    return str(parsed.name).lower().replace("_", "-")


def licence_of(dist_name: str) -> str:
    meta = distribution(dist_name).metadata
    parts = [meta.get("License-Expression") or "", meta.get("License") or ""]
    parts += [c for c in meta.get_all("Classifier") or [] if c.startswith("License ::")]
    return " | ".join(p.strip() for p in parts if p and len(p) < 300)


def resolve(root: str) -> dict[str, str]:
    seen: dict[str, str] = {}
    stack = [root]
    while stack:
        name = stack.pop()
        if name in seen:
            continue
        try:
            dist = distribution(name)
        except PackageNotFoundError:
            seen[name] = "NOT INSTALLED"
            continue
        seen[name] = licence_of(name)
        for req in dist.requires or []:
            dep = _name(req)
            if dep and dep not in seen:
                stack.append(dep)
    return seen


def main(argv: list[str]) -> int:
    root = argv[1] if len(argv) > 1 else "causal-core"
    deps = resolve(root)
    deps.pop(root, None)
    bad = []
    for name, lic in sorted(deps.items()):
        if name in KNOWN:
            verdict = f"ok (reviewed: {KNOWN[name]})"
        elif lic == "NOT INSTALLED":
            verdict = "FAIL: not installed, cannot inspect"
        elif COPYLEFT.search(lic) and not PERMISSIVE.search(lic.split("|")[0]):
            verdict = "FAIL: copyleft"
        elif PERMISSIVE.search(lic):
            verdict = "ok"
        else:
            verdict = "FAIL: unclassified"
        if verdict.startswith("FAIL"):
            bad.append(name)
        print(f"{name:28} {verdict:40} {lic[:90]}")
    print(f"\n{len(deps)} runtime dependencies inspected for {root}; {len(bad)} failing")
    if not deps:
        print("inspected nothing: failing")
        return 1
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
