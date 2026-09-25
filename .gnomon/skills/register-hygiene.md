+++
name = "register hygiene"
description = "How the declared-not-implemented register works: adding an entry, re-citing a moved line, and closing an entry without dropping it"
match = '\b(register|declared[_ -]not[_ -]implemented|dni|left_the_list|claimed_in|re-?cite|citation|citations|drift\w*|status[_ ]claims|current_stage|overclaim\w*|not implemented)\b'
+++

`conformance/declared_not_implemented.json` lists capabilities this
repository's docs (or its own API contracts) CLAIM and the code does not do as
claimed. `docs/CURRENT_STAGE.md` shows the same status token for each.
`scripts/status_claims.py` holds them to each other and to the tree, and runs
in `.gnomon/ci.sh`.

**Shape of an entry.** `id`, `status` (`declared-not-implemented` | `partial`
| `planned`), `capability`, `claimed_in` (where the claim is made),
`reality` (one line: what the code actually does), `evidence` (code locations
that make `reality` true), `leaves_by` (`kind` test | ci-run | retraction,
a `path`, and what it `asserts`). Every citation is `{at, says}`: `at` is
`file:line` (or `file@rev:line` for a claim withdrawn from the tree but
published at that commit) and `says` is a verbatim quote from that line.

**When you find a new overclaim**: add an entry, and a `dni:<id>` row on
`docs/CURRENT_STAGE.md`. Quote the claim exactly; a paraphrase is not a
citation. `claimed_in` may be empty only for `planned`.

**When you edit a file the register cites** (docs/AGENTS.md, the package
READMEs, docs/ROADMAP.md, ...):

1. Run `python3 scripts/status_claims.py` BEFORE the edit, so a drift you see
   afterwards is yours.
2. After the edit, run it again. `drifted -- the quote is now at line N` means
   the line moved: update `at` to the new line in the same change (drift is a
   warning, not a failure -- do not leave it). `no longer says ...` is a
   failure -- the quote no longer exists anywhere in the file, so the register now asserts
   something untrue. Either you retracted the claim (then the entry leaves,
   below) or you reworded it (then re-quote it).
3. Never delete an entry to make the gate pass.

**Closing an entry.** An entry leaves by being IMPLEMENTED -- its `leaves_by`
test exists and passes -- or by the claim being RETRACTED from the docs. Never
by being dropped. Move it to `left_the_list` as
`{id, was, fixed_in: <commit>, pinned_by: <test path>}`, and change its
CURRENT_STAGE row to `implemented` with a link to that test, in the same
change. `status_claims.py` fails a `pinned_by` path that does not exist, and
warns when a `leaves_by` path has appeared (run it; if it passes, the entry is
ready to leave).

**What the page may not say.** No ✅, no "Phase 1 complete", no DONE, no
cross-repo consolidation language -- status_claims.py fails on each. A status
is one of the four words, with its evidence.

**Who writes it.** In the gnomon chain the coordinator and the implementor
may both write `conformance/**`. When another agent owns the register in a
parallel change, list the needed edits (entry id, old `at`, new `at`) in your
report instead of editing it.
