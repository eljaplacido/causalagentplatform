#!/usr/bin/env python3
"""coverage_gate -- fail when backend line coverage drops below the committed floor.

A ratchet, not a target (the shape of gnomon's scripts/coverage_gate.mjs):

  * the floor lives in scripts/coverage-floor.json and is raised deliberately,
    never by the last run -- a floor that follows the last run turns one lucky
    commit into a permanent obligation, and a gate people cannot satisfy is a
    gate people delete;
  * it replaces the global `--cov-fail-under=80` that used to sit in
    backend/pyproject.toml addopts. That flag made EVERY partial run fail (the
    unit suite alone measures ~75%), so the CI unit job could not pass and a
    developer running one test file got a coverage failure for code they never
    touched. Coverage is a property of the full suite, so it is judged here, on
    the full suite, once;
  * the committed floor may only go UP. Compared against the same file at the
    merge base with main; a lower floor fails unless the commit range carries a
    `Ratchet-Exempt: <why>` trailer (or RATCHET_EXEMPT is set), which puts the
    reason where a reviewer sees it.

It reads coverage.py's JSON report rather than scraping the terminal table, and
fails closed: a missing or unreadable report is "the coverage run did not
report", which is a different failure from "coverage fell" and is worded so.

Usage:
  scripts/coverage_gate.py <coverage.json>
"""

from __future__ import annotations

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

FLOOR_FILE = "scripts/coverage-floor.json"


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        bad("usage: coverage_gate.py <coverage.json>")
        return 2
    report = Path(argv[1])
    try:
        totals = load_json(report)["totals"]
        measured = float(totals["percent_covered"])
        covered = int(totals["covered_lines"])
        statements = int(totals["num_statements"])
    except (OSError, ValueError, KeyError) as exc:
        bad(f"coverage run did not report ({report}: {exc!s:.120}) -- refusing to pass. "
            "This is NOT a coverage regression; the runner produced no JSON.")
        return 2

    if statements == 0:
        bad("coverage report measured 0 statements -- the gate inspected nothing.")
        return 1

    floor_doc = load_json(ROOT / FLOOR_FILE)
    floor = float(floor_doc["line_percent"])
    failed = False

    if measured + 1e-9 < floor:
        bad(f"backend line coverage {measured:.2f}% ({covered}/{statements}) is below "
            f"the floor of {floor:.2f}% in {FLOOR_FILE}")
        failed = True
    else:
        ok(f"backend line coverage {measured:.2f}% ({covered}/{statements} statements) "
           f">= floor {floor:.2f}%")
        if measured - floor >= 1.0:
            warn(f"the floor can be raised deliberately in {FLOOR_FILE}: measured "
                 f"{measured:.2f}% vs floor {floor:.2f}%")

    status, old = baseline_at_base(FLOOR_FILE)
    if status == "found":
        old_floor = float(old["line_percent"])
        if floor + 1e-9 < old_floor:
            reason = ratchet_exempt_reason()
            if reason:
                warn(f"floor LOWERED {old_floor:.2f}% -> {floor:.2f}%; Ratchet-Exempt: {reason}")
            else:
                bad(f"{FLOOR_FILE} lowered the floor {old_floor:.2f}% -> {floor:.2f}%. "
                    "A coverage floor only goes up. If this is deliberate, say why in the "
                    "commit message with a `Ratchet-Exempt: <why>` line.")
                failed = True
        else:
            ok(f"floor did not go down since the merge base ({old_floor:.2f}% -> {floor:.2f}%)")
    else:
        warn(describe_base_status(status, FLOOR_FILE))

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
