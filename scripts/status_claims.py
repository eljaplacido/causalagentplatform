#!/usr/bin/env python3
"""status_claims -- the status page and the declared-not-implemented register must agree with the tree.

gnomon's rule for its own README: a claim about behaviour must be measured,
pinned by a test, or declared unproven. This script is where that rule becomes
a gate for docs/CURRENT_STAGE.md and conformance/declared_not_implemented.json.

It checks, and fails on:

  register (conformance/declared_not_implemented.json)
    * shape: unique ids, a known status, a reality line, a leaves_by with a kind
      and a path; claimed_in may be empty only for `planned`
    * every citation ({at: 'file:line' or 'file@rev:line', says: quote}) in
      claimed_in and evidence: the quote must still be in the cited file. On
      another line it is reported as drift (not failed -- a line moved); GONE
      is a failure, because the register would then assert something about the
      docs or the code that is no longer true
    * every left_the_list entry is pinned by a test path that exists

  status page (docs/CURRENT_STAGE.md)
    * every status cell is one of: implemented | partial | planned |
      declared-not-implemented
    * every `implemented` row links to a test file that exists
    * every other row names a register id (dni:<id>) whose status matches
    * every OPEN register entry appears on the page (identity: the page cannot
      quietly drop a known gap)
    * no ✅, no "Phase 1 complete"/"DONE", no cross-repo consolidation names --
      the overclaims the Sep 2026 audit removed may not come back unnoticed

It reports, without failing:
    * a leaves_by test path that now exists -- run it; if it passes, the entry
      is ready to leave the list

Usage: scripts/status_claims.py        exit 0 ok, 1 a check failed
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _gatelib import ROOT, bad, load_json, ok, warn  # noqa: E402

REGISTER = ROOT / "conformance" / "declared_not_implemented.json"
STAGE = ROOT / "docs" / "CURRENT_STAGE.md"
STATUSES = {"implemented", "partial", "planned", "declared-not-implemented"}
OPEN_STATUSES = STATUSES - {"implemented"}
LEAVE_KINDS = {"test", "ci-run", "retraction"}
FORBIDDEN_ON_PAGE = [
    (r"✅", "a check-mark status: every row states a status word and its evidence instead"),
    (r"(?i)phase\s*1\b[^|\n]{0,40}\bcomplete", "the Phase 1 completion claim the audit withdrew"),
    (r"\bDONE\b", "a DONE marker"),
    (r"(?i)carfdevops|causalrust|projectcarfcynepic",
     "cross-repo consolidation language (those repos were inspiration only, not merged here)"),
]
TEST_PATH = re.compile(r"(^|/)(tests?|__tests__|e2e)/|(^|/)test_[^/]+\.py$|\.(test|spec)\.tsx?$")
CITATION = re.compile(r"^(?P<file>[^@:]+)(?:@(?P<rev>[0-9a-f]{7,40}))?:(?P<line>\d+)$")

failures = 0


def fail(msg: str) -> None:
    global failures
    failures += 1
    bad(msg)


def file_lines(path: str, rev: str | None) -> list[str] | None:
    if rev:
        proc = subprocess.run(["git", "show", f"{rev}:{path}"], cwd=ROOT,
                              capture_output=True, text=True, check=False)
        return proc.stdout.splitlines() if proc.returncode == 0 else None
    p = ROOT / path
    return p.read_text(encoding="utf-8").splitlines() if p.is_file() else None


def check_citation(owner: str, field: str, cite: dict) -> str:
    """Return 'ok' | 'drift' | 'unverifiable' | 'fail'."""
    at, says = str(cite.get("at", "")), str(cite.get("says", ""))
    m = CITATION.match(at)
    if not m or not says:
        fail(f"{owner}: {field} citation {cite!r} needs at='file:line' (or file@rev:line) and a quote")
        return "fail"
    path, rev, line = m["file"], m["rev"], int(m["line"])
    lines = file_lines(path, rev)
    if lines is None:
        if rev:
            warn(f"{owner}: {at} -- revision {rev} is not in this clone (shallow?); citation unverifiable here")
            return "unverifiable"
        fail(f"{owner}: {field} cites {path}, which does not exist")
        return "fail"
    if 1 <= line <= len(lines) and says in lines[line - 1]:
        return "ok"
    hits = [i + 1 for i, text in enumerate(lines) if says in text]
    if hits:
        warn(f"{owner}: {at} drifted -- the quote is now at line {hits[0]}; update the citation")
        return "drift"
    fail(f"{owner}: {field} {at} no longer says {says!r}. If the claim was retracted or the "
         "code fixed, the entry may be ready to leave the list; otherwise re-cite it.")
    return "fail"


def check_register(reg: dict) -> tuple[dict[str, dict], int, int]:
    entries = reg.get("declared_not_implemented", [])
    if not entries:
        fail("the register lists no entries -- a check that inspected nothing may not report success")
    by_id: dict[str, dict] = {}
    citations = drifted = 0
    for e in entries:
        eid = e.get("id", "")
        if not eid or eid in by_id:
            fail(f"register: missing or duplicate id {eid!r}")
            continue
        by_id[eid] = e
        status = e.get("status")
        if status not in OPEN_STATUSES:
            fail(f"{eid}: status {status!r} is not one of {sorted(OPEN_STATUSES)}")
        if not str(e.get("reality", "")).strip():
            fail(f"{eid}: no reality line")
        if not e.get("claimed_in") and status != "planned":
            fail(f"{eid}: claimed_in is empty -- only a `planned` entry may have no claim to cite")
        lb = e.get("leaves_by") or {}
        if lb.get("kind") not in LEAVE_KINDS or not lb.get("path") or not lb.get("asserts"):
            fail(f"{eid}: leaves_by needs kind in {sorted(LEAVE_KINDS)}, a path, and what it asserts")
        elif lb["kind"] == "test" and (ROOT / lb["path"]).exists():
            warn(f"{eid}: its leaves_by path {lb['path']} exists -- run it; if it passes, move "
                 "this entry to left_the_list")
        for field in ("claimed_in", "evidence"):
            for cite in e.get(field, []):
                citations += 1
                if check_citation(eid, field, cite) == "drift":
                    drifted += 1
    for gone in reg.get("left_the_list", []):
        gid = gone.get("id", "")
        if gid in by_id:
            fail(f"{gid}: is both open and in left_the_list")
        pin = gone.get("pinned_by", "")
        if not pin or not (ROOT / pin).exists():
            fail(f"{gid}: left the list but its pinned_by test {pin!r} does not exist")
    return by_id, citations, drifted


ROW = re.compile(r"^\|(.+)\|\s*$")


def check_page(by_id: dict[str, dict]) -> tuple[int, int, int]:
    if not STAGE.exists():
        fail("docs/CURRENT_STAGE.md does not exist")
        return 0, 0, 0
    text = STAGE.read_text(encoding="utf-8")
    for pattern, why in FORBIDDEN_ON_PAGE:
        for i, line in enumerate(text.splitlines(), 1):
            hit = re.search(pattern, line)
            if hit:
                fail(f"docs/CURRENT_STAGE.md:{i} contains {hit.group(0)!r} -- {why}")
    rows = implemented = linked = 0
    referenced: set[str] = set()
    for i, line in enumerate(text.splitlines(), 1):
        m = ROW.match(line.strip())
        if not m:
            continue
        cells = [c.strip() for c in m.group(1).split("|")]
        status_cells = [c.strip("` *") for c in cells if c.strip("` *") in STATUSES]
        if not status_cells:
            continue
        rows += 1
        status = status_cells[0]
        ids = re.findall(r"dni:([a-z0-9-]+)", line)
        referenced.update(ids)
        if status == "implemented":
            targets = re.findall(r"\]\(([^)]+)\)", line)
            tests = [t for t in targets if TEST_PATH.search(t)]
            if not tests:
                fail(f"docs/CURRENT_STAGE.md:{i}: an `implemented` row must link to a test file")
                continue
            for t in tests:
                resolved = (STAGE.parent / t).resolve()
                if not resolved.exists():
                    fail(f"docs/CURRENT_STAGE.md:{i}: linked test {t} does not exist")
            implemented += 1
        else:
            if not ids:
                fail(f"docs/CURRENT_STAGE.md:{i}: a `{status}` row must name its register entry (dni:<id>)")
                continue
            for eid in ids:
                if eid not in by_id:
                    fail(f"docs/CURRENT_STAGE.md:{i}: dni:{eid} is not in the register")
                elif by_id[eid].get("status") != status:
                    fail(f"docs/CURRENT_STAGE.md:{i}: row says {status!r} but dni:{eid} says "
                         f"{by_id[eid].get('status')!r}")
            linked += 1
    if rows == 0:
        fail("docs/CURRENT_STAGE.md has no status rows -- a check that inspected nothing may not report success")
    missing = sorted(set(by_id) - referenced)
    if missing:
        fail(f"docs/CURRENT_STAGE.md does not mention {len(missing)} open register entr"
             f"{'y' if len(missing) == 1 else 'ies'}: {', '.join(missing)}")
    return rows, implemented, linked


def main() -> int:
    reg = load_json(REGISTER)
    by_id, citations, drifted = check_register(reg)
    rows, implemented, linked = check_page(by_id)
    counts: dict[str, int] = {}
    for e in by_id.values():
        counts[e.get("status", "?")] = counts.get(e.get("status", "?"), 0) + 1
    breakdown = ", ".join(f"{n} {s}" for s, n in sorted(counts.items()))
    summary = (f"register: {len(by_id)} open ({breakdown}), {len(reg.get('left_the_list', []))} left the "
               f"list; {citations} citations checked ({drifted} drifted); CURRENT_STAGE: {rows} rows "
               f"({implemented} implemented with tests, {linked} linked to the register)")
    if failures:
        bad(f"status claims: {failures} failure(s) -- {summary}")
        return 1
    ok(f"status claims: {summary}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
