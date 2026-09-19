#!/usr/bin/env python3
"""doc_reconciliation -- mark a document for re-reading when the code it describes moves.

Python port of gnomon's scripts/doc_reconciliation.mjs, with one deliberate
change: the marker is a CONTENT HASH of the described code, not a commit SHA.
gnomon records its own failure with SHAs -- a doc blessed at a commit that was
later amended pointed at a SHA that existed only in one reflog, and the check
broke on every fresh clone. A hash of the subjects' contents survives amend,
rebase, squash and shallow clones, and it answers the question directly: has
what this document describes changed since somebody last read it?

THE PROBLEM THIS IS FOR. Structured claims can be pinned by tests; prose cannot,
and prose is the half that rots. The Sep 2026 audit of this repository found the
docs claiming a finished Phase 1, SCM counterfactuals, bootstrap confidence
intervals and LLM routing that the code does not do. None of that is
mechanically checkable as a sentence. What IS mechanical is "has the code this
document describes changed since anybody last read it against the code?"

WHAT IT DOES NOT DO, said plainly so nobody reads more into a green tick. It
cannot tell whether a document is true. It tells you which documents are OWED a
reading. conformance/declared_not_implemented.json is where known-false claims
are pinned; this only schedules the reading.

The manifest (docs/reconciliation.json) holds, per document:
  subjects     paths (files or directories) whose change should send someone
               back to the prose
  baseline     {"on": date, "sha256": {subject: hash}} -- the subjects' content
               when the document was last read against them (or, for a
               document nobody has read yet, when tracking began)
  last_read    null, or {"on": date, "by": who/what} -- null means NEVER read
               against the code since tracking began, which is itself owed

Two kinds of failure are hard failures, not reports, because they mean this
check is looking at less than it claims (gnomon's gate_scope rule):
  * a Markdown file in docs/ (or README.md) that the manifest does not track;
  * a subject path that no longer matches any file (renamed or deleted -- the
    doc would silently stop being watched).

Usage:
  scripts/doc_reconciliation.py                  # report; exit 1 only on manifest problems
  scripts/doc_reconciliation.py --check          # also exit 1 if any document is owed
  scripts/doc_reconciliation.py --bless docs/X.md [...] --by "<who read it>"
  scripts/doc_reconciliation.py --baseline-new   # record baselines for docs missing one

`--bless` is the forcing function, not a lock: anybody can run it without
reading anything. The point is that the claim "this was read against the code on
<date> by <who>" lands in a file a reviewer sees.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _gatelib import ROOT, bad, dump_json, load_json, ok, warn  # noqa: E402

MANIFEST = ROOT / "docs" / "reconciliation.json"


def tracked_docs() -> set[str]:
    """Every document the manifest MUST track: docs/*.md and the root README."""
    docs = {f"docs/{p.name}" for p in (ROOT / "docs").glob("*.md")}
    if (ROOT / "README.md").exists():
        docs.add("README.md")
    return docs


def subject_files(subject: str) -> list[str]:
    """Tracked + untracked-but-not-ignored files under a subject path, sorted."""
    proc = subprocess.run(
        ["git", "ls-files", "-co", "--exclude-standard", "-z", "--", subject],
        cwd=ROOT, capture_output=True, check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git ls-files failed for {subject}: {proc.stderr.decode()[:200]}")
    return sorted({f for f in proc.stdout.decode().split("\0") if f})


def subject_hash(subject: str) -> tuple[str | None, int]:
    """sha256 over (path, content hash) of every file under the subject; None if it matches nothing."""
    files = subject_files(subject)
    if not files:
        return None, 0
    h = hashlib.sha256()
    for rel in files:
        path = ROOT / rel
        if path.is_symlink():
            digest = "symlink:" + os.readlink(path)
        elif path.is_file():
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        else:
            digest = "missing"
        h.update(f"{rel}\0{digest}\n".encode())
    return h.hexdigest(), len(files)


def today() -> str:
    return dt.date.today().isoformat()


def main() -> int:
    ap = argparse.ArgumentParser(description="doc_reconciliation")
    ap.add_argument("--check", action="store_true", help="exit 1 if any document is owed a reading")
    ap.add_argument("--bless", nargs="+", metavar="DOC", help="record that DOC was read against its subjects now")
    ap.add_argument("--by", default="", help="who/what read it (required with --bless)")
    ap.add_argument("--baseline-new", action="store_true",
                    help="record a baseline for documents that have none (does NOT mark them read)")
    args = ap.parse_args()

    manifest = load_json(MANIFEST)
    docs: dict[str, dict] = manifest["docs"]

    if args.bless:
        if not args.by.strip():
            bad("--bless needs --by '<who read it>': the record is the point")
            return 2
        for doc in args.bless:
            if doc not in docs:
                bad(f"not in the manifest: {doc}")
                return 2
            hashes = {}
            for s in docs[doc]["subjects"]:
                digest, _ = subject_hash(s)
                if digest is None:
                    bad(f"{doc}: subject {s} matches no file -- fix the manifest before blessing")
                    return 1
                hashes[s] = digest
            docs[doc]["baseline"] = {"on": today(), "sha256": hashes}
            docs[doc]["last_read"] = {"on": today(), "by": args.by.strip()}
        dump_json(MANIFEST, manifest)
        ok(f"blessed {len(args.bless)} document(s) on {today()} by {args.by.strip()}")
        return 0

    if args.baseline_new:
        n = 0
        for doc, spec in docs.items():
            if spec.get("baseline"):
                continue
            hashes = {}
            for s in spec["subjects"]:
                digest, _ = subject_hash(s)
                if digest is None:
                    bad(f"{doc}: subject {s} matches no file")
                    return 1
                hashes[s] = digest
            spec["baseline"] = {"on": today(), "sha256": hashes}
            spec.setdefault("last_read", None)
            n += 1
        dump_json(MANIFEST, manifest)
        ok(f"recorded baselines for {n} document(s); none marked read")
        return 0

    problems = 0
    expected = tracked_docs()
    untracked = sorted(expected - set(docs))
    if untracked:
        bad(f"{len(untracked)} document(s) not in docs/reconciliation.json -- add each with its subjects:")
        for d in untracked:
            print(f"       {d}")
        problems += 1
    gone = sorted(d for d in docs if not (ROOT / d).exists())
    if gone:
        bad(f"manifest tracks document(s) that no longer exist: {', '.join(gone)}")
        problems += 1

    changed: list[tuple[str, list[str]]] = []
    never_read: list[str] = []
    files_hashed = 0
    for doc in sorted(docs):
        spec = docs[doc]
        if doc in gone:
            continue
        has_baseline = bool(spec.get("baseline"))
        base = (spec.get("baseline") or {}).get("sha256", {})
        moved = []
        for s in spec.get("subjects", []):
            digest, n = subject_hash(s)
            files_hashed += n
            if digest is None:
                bad(f"{doc}: subject {s!r} matches no file -- renamed or deleted? The doc is no "
                    "longer watched for it. Fix the subject list.")
                problems += 1
                continue
            # No baseline at all is "never read", not "everything moved": there
            # is nothing to have moved FROM. --baseline-new records one.
            if has_baseline and base.get(s) != digest:
                moved.append(s)
        if not spec.get("subjects"):
            bad(f"{doc}: declares no subjects, so nothing could ever mark it owed")
            problems += 1
        if moved:
            changed.append((doc, moved))
        elif spec.get("last_read") is None or not has_baseline:
            never_read.append(doc)

    total = len(docs)
    print(f"doc reconciliation: {total} document(s) tracked, {files_hashed} subject file(s) hashed")
    if changed:
        print(f"  {len(changed)} owed a re-read -- the code they describe changed since they were last read:")
        for doc, moved in changed:
            last = docs[doc].get("last_read")
            since = f"last read {last['on']} by {last['by']}" if last else "never read; baseline " + \
                str((docs[doc].get("baseline") or {}).get("on", "missing"))
            print(f"    {doc}  ({since})")
            print(f"      moved: {', '.join(moved)}")
            print(f"      after reading it: scripts/doc_reconciliation.py --bless {doc} --by '<you>'")
    if never_read:
        print(f"  {len(never_read)} never read against the code since tracking began (subjects unchanged):")
        for doc in never_read:
            print(f"    {doc}  -- {docs[doc].get('why', '')}")
    current = total - len(changed) - len(never_read) - len(gone)
    print(f"  {current} read and current")

    if problems:
        return 1
    if args.check and (changed or never_read):
        return 1
    if files_hashed == 0:
        bad("hashed 0 subject files -- a check that inspected nothing may not report success")
        return 1
    if changed or never_read:
        warn("owed readings are reported, not failed -- `--check` makes them fail")
    return 0


if __name__ == "__main__":
    sys.exit(main())
