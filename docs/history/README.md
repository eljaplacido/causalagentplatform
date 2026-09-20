# History rewrite, 2026-09-20 (before the first push)

The repository's history was rewritten once, before it was ever pushed, to remove a file that
held a plaintext API key (`api.txt.txt`), a few personal tooling directories (`.opencode`,
`.openfang`, `.antigravity`, `.cursor`, `goodprompts`), a stray `=1.11.0` artefact, and two
personal identifiers (a private IP address and a machine hostname). It was done with
`git filter-repo`, so **every commit id changed**.

`commit-map-2026-09-20-prepush-rewrite.txt` is the map `git filter-repo` produced: old id, then
new id, one pair per line. It is committed so that any commit id quoted anywhere -- in
`conformance/declared_not_implemented.json`, in `docs/CURRENT_STAGE.md`, in the ratchet baselines,
or in a benchmark results file -- can be traced back to the run that produced it. Those quoted ids
were translated through this map in the same change, so they point at the new history.

The rewrite changed no file content other than the removals and redactions above. The commit
sequence, messages, dates and authorship are unchanged, which matters here: the benchmark
discipline in this repository rests on each pre-registration being committed *before* the run it
governs, and that ordering is still visible in the history.
