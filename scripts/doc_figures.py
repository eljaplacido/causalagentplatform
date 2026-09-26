#!/usr/bin/env python3
"""doc_figures -- every number the docs quote from a benchmark matches the run it came from.

status_claims checks what a document says a capability's *status* is; nothing checked
the *figures*. A sibling project's report once said "39/39 A+" of a programme whose own
generated results evaluated nine hypotheses and passed eight. The same drift here would
look like README.md still quoting 94.9% after T0's standing run moved.

scripts/doc-figures.json binds each quoted figure to the value it came from:

    doc     the document, and `says`, a verbatim substring of it that contains the figure
    figure  the number as written ("94.9", "1,794", "-15,205", "800")
    bench   the benchmarks/ directory whose STANDING run it comes from -- the latest
            passing run, from a clean tree, recorded against the current
            PRE-REGISTRATION.md's exact bytes (the run test_benchmark_results requires).
            "From a clean tree" means its `git` is a full 40-hex commit, no `-dirty`,
            that is an ancestor of HEAD: `unknown`, a short sha or a commit this history
            does not contain cannot be traced to code, so it never stands.
    path    where in that run's JSON: "/a/b/*/c", with `*` over dict values or list items
            and `name[key=v1|v2]` keeping only the list items whose `key` is listed;
            `~1` stands for a `/` inside a key (RFC 6901)
    agg     one | min | max | count | count_true
    scale   multiply before comparing (100 for a percentage)
    round   nearest (default) | outward (min rounds down, max up: how a range is quoted)

The figure matches when the value, rounded to the figure's own decimals, equals it.

Usage: scripts/doc_figures.py      exit 0 ok, 1 a figure does not match or cannot be found
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "scripts" / "doc-figures.json"
SHA = re.compile(r"^[0-9a-f]{40}$")
FILTER = re.compile(r"^(?P<name>[^\[]+)\[(?P<key>[^=\]]+)=(?P<vals>[^\]]+)\]$")


def traceable_commit(git: object) -> bool:
    """A run's `git` names a committed tree this history contains (40-hex, no -dirty, an
    ancestor of HEAD). Not merely "no -dirty suffix": `unknown` passed that for a day."""
    sha = str(git or "")
    if not SHA.match(sha):
        return False
    proc = subprocess.run(["git", "-C", str(ROOT), "merge-base", "--is-ancestor", sha, "HEAD"],
                          capture_output=True, check=False)
    return proc.returncode == 0


def standing_run(bench: str) -> tuple[Path, dict[str, Any]]:
    root = ROOT / "benchmarks" / bench
    raw = (root / "PRE-REGISTRATION.md").read_bytes()
    current = "sha256:" + hashlib.sha256(raw).hexdigest()
    runs = []
    for p in sorted((root / "results").glob("*.json")):
        doc = json.loads(p.read_text())
        if (doc.get("prereg_sha256") == current and doc.get("passed") is True
                and traceable_commit(doc.get("git"))):
            runs.append((p, doc))
    if not runs:
        raise LookupError(f"{bench}: no passing clean run against the current registration")
    return runs[-1]


def select(node: Any, path: str) -> list[Any]:
    values = [node]
    for raw in [p for p in path.split("/") if p]:
        part = raw.replace("~1", "/").replace("~0", "~")  # RFC 6901 escapes
        nxt: list[Any] = []
        m = FILTER.match(part)
        name = m.group("name") if m else part
        for v in values:
            if name == "*":
                items = list(v.values()) if isinstance(v, dict) else list(v)
            else:
                if not isinstance(v, dict) or name not in v:
                    raise LookupError(f"no {name!r} at this point of {path}")
                items = [v[name]]
            for item in items:
                if m and isinstance(item, list):
                    keep = set(m.group("vals").split("|"))
                    nxt.extend(x for x in item if str(x.get(m.group("key"))) in keep)
                else:
                    nxt.append(item)
        values = nxt
    return values


def aggregate(values: list[Any], agg: str) -> float:
    if agg == "count":
        return float(len(values[0]) if len(values) == 1 and isinstance(values[0], (list, dict))
                     else len(values))
    if agg == "count_true":
        flat = list(values[0].values()) if len(values) == 1 and isinstance(values[0], dict) \
            else values
        return float(sum(1 for v in flat if v is True))
    nums = [float(v) for v in values if v is not None]
    if not nums:
        raise LookupError("the path selected no numbers")
    if agg == "one":
        if len(nums) != 1:
            raise LookupError(f"agg 'one' needs exactly one value, the path selected {len(nums)}")
        return nums[0]
    return min(nums) if agg == "min" else max(nums)


def parse_figure(text: str) -> tuple[float, int]:
    clean = text.replace(",", "").replace("−", "-").strip()
    decimals = len(clean.split(".")[1]) if "." in clean else 0
    return float(clean), decimals


def matches(value: float, figure: str, agg: str, rounding: str) -> bool:
    want, dec = parse_figure(figure)
    f = 10**dec
    if rounding == "outward" and agg in ("min", "max"):
        got = math.floor(value * f + 1e-9) / f if agg == "min" else math.ceil(value * f - 1e-9) / f
    else:
        got = round(value * f) / f
    return abs(got - want) < 0.5 / f


def main() -> int:
    manifest = json.loads(MANIFEST.read_text())
    failures = 0
    checked = 0
    for entry in manifest["figures"]:
        where = f"{entry['doc']}: {entry['figure']!r}"
        text = (ROOT / entry["doc"]).read_text(encoding="utf-8")
        if entry["says"] not in text:
            print(f"FAIL {where} -- the doc no longer says {entry['says']!r}")
            failures += 1
            continue
        if entry["figure"] not in entry["says"]:
            print(f"FAIL {where} -- the figure is not inside its own quote")
            failures += 1
            continue
        try:
            path, run = standing_run(entry["bench"])
            value = aggregate(select(run, entry["path"]), entry.get("agg", "one"))
        except (LookupError, KeyError, TypeError, ValueError) as exc:
            print(f"FAIL {where} -- {exc}")
            failures += 1
            continue
        value *= float(entry.get("scale", 1))
        checked += 1
        if not matches(value, entry["figure"], entry.get("agg", "one"),
                       entry.get("round", "nearest")):
            print(f"FAIL {where} -- the standing run {path.relative_to(ROOT)} gives {value:.6g}")
            failures += 1
    docs = len({e["doc"] for e in manifest["figures"]})
    if failures:
        print(f"FAIL doc figures: {failures} of {len(manifest['figures'])} do not match")
        return 1
    if checked == 0:
        print("FAIL doc figures: nothing was checked")
        return 1
    print(f"ok   doc figures: {checked} figures in {docs} documents match their standing runs")
    return 0


if __name__ == "__main__":
    sys.exit(main())
