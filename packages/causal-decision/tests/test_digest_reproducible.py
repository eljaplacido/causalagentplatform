"""A replayed decision reproduces its digest whatever the host's thread count (septa-89).

``ledger.replays`` compares a re-run decision with a recorded one by ``decision_digest``,
which covers the evidence and so the analysis's ``result_digest``, which covers every
number and string in the result. Two things used to move it between processes that
were given the same data:

* the BLAS thread count: a multithreaded BLAS sums in a thread-dependent order, so the
  floats moved in their last bits between 1, 2 and 4+ threads (seen on the gate's
  20-core host at 50,000 rows); and
* the string hash seed: the rendered ID expression listed its factors in set order.

Preconditions. The kernel's own environment; each run is a fresh interpreter whose
``OMP_NUM_THREADS``/``OPENBLAS_NUM_THREADS``/``MKL_NUM_THREADS`` and ``PYTHONHASHSEED``
are set before numpy loads, so each one is what a different host would be.

Postconditions (from the bead and ``ledger.replays``'s docstring: "re-deciding produced
the same verdict, byte for byte"). Every run yields the same ``result_digest``,
``decision_digest`` and ledger ``entry_digest``; and the decision is about the effect the
structural model sets (2.0 by construction), so the digests agree on a correct answer.
The same holds on #107's paths (contract 0.15.0): a paired design, a treatment that never
varied (NO_VARIATION, an experiment) and an estimate declared for another population (not
transported, an experiment there).

Undefined here. The same digests on another CPU family or BLAS build (an OpenBLAS with
DYNAMIC_ARCH picks kernels per CPU; causal_core.numerics says so) -- nothing is asserted.

The negative controls put each defect back and require the digests to differ: without
them, a probe too small for BLAS to thread would pass the positive test while measuring
nothing.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

TRUE_EFFECT = 2.0

#: One decision through the real path. argv[1] names a defect to put back ("" for none).
PROBE = r"""
import contextlib, json, sys
import networkx as nx
import numpy as np, pandas as pd
import causal_core as cc
import causal_core.pipeline as pipeline
from causal_core.identification.id_algorithm import ADMG
from causal_decision.contract import DecisionRequest
from causal_decision.ledger import append
from causal_decision.policy import decide

defect = sys.argv[1]
if defect == "unpinned_blas":
    pipeline.pinned = contextlib.nullcontext
elif defect == "hash_ordered_components":
    def c_components(self):
        g = nx.Graph(); g.add_nodes_from(self.nodes)
        for p in self.bidirected:
            a, b = tuple(p); g.add_edge(a, b)
        return [frozenset(c) for c in nx.connected_components(g)]
    ADMG.c_components = c_components
elif defect == "hash_ordered_transport_note":
    # The not-transported warning naming its populations in set order (#107's path).
    from causal_core.contract import CausalResult
    from causal_core.canonical import digest
    def _not_transported(out):
        q = getattr(out, "query", None)
        if not isinstance(out, CausalResult) or not q.target_population:
            return out
        note = "not transported: " + " / ".join({q.population, q.target_population})
        res = out.model_copy(update={"warnings": (*out.warnings, note)})
        return res.model_copy(update={"result_digest": digest(
            res.model_dump(mode="json", exclude={"result_digest"}))})
    pipeline._not_transported = _not_transported

# The structural model: twelve confounders, a binary treatment, effect TRUE_EFFECT.
n, k = 50_000, 12
rng = np.random.default_rng(89)
Z = rng.normal(size=(n, k))
x = rng.binomial(1, 1 / (1 + np.exp(-0.3 * Z[:, :3].sum(axis=1))))
y = TRUE_EFFECT * x + Z @ rng.normal(size=k) + rng.normal(size=n)
df = pd.DataFrame(Z, columns=[f"z{i}" for i in range(k)])
df["x"] = x.astype(float)
df["y"] = y
graph = "; ".join(f"z{i} -> x; z{i} -> y" for i in range(k)) + "; x -> y"

out = cc.analyze(df, graph, treatment="x", outcome="y")
request = DecisionRequest(action="ship x", outcome="y", direction="increase",
                          minimum_worthwhile_effect=1.0, harm_threshold=0.0)
d = decide(out, request)
rec = append(None, d, out, recorded_at="2026-10-31T00:00:00+00:00")
# The same decision sealed as a shadow one (septa-95): its tag is in the record.
shadow = append(None, d, out, recorded_at="2026-10-31T00:00:00+00:00", shadow="S-089")

# #107's paths, through the same pinned analyze: a paired design (2,000 pairs, effect
# TRUE_EFFECT within each pair), a treatment that never varied (NO_VARIATION -> an
# experiment), and the first analysis declared for another population (not transported
# -> an experiment there). Each is decided and sealed like the first.
def sealed(o, req):
    dd = decide(o, req)
    return dd, append(None, dd, o, recorded_at="2026-10-31T00:00:00+00:00")

pairs = 2_000
pair_effect = rng.normal(size=pairs)
pf = pd.DataFrame({
    "pair": np.repeat(np.arange(pairs), 2),
    "x": np.tile([0.0, 1.0], pairs),
})
pf["y"] = np.repeat(pair_effect, 2) + TRUE_EFFECT * pf["x"] + rng.normal(size=2 * pairs)
blocked = cc.analyze(pf, "x -> y", treatment="x", outcome="y", design="randomized",
                     block="pair")
db, rb = sealed(blocked, request)

flat = df.assign(x=1.0)
novar = cc.analyze(flat, graph, treatment="x", outcome="y")
dn, rn = sealed(novar, DecisionRequest(action="ship x", outcome="y", direction="increase",
                                       minimum_worthwhile_effect=1.0, harm_threshold=0.0,
                                       can_randomize=True, randomization_unit="customer",
                                       units_per_day=400))

away = cc.analyze(df, graph, treatment="x", outcome="y", population="local lane",
                  target_population="hosted lane")
dt, rt = sealed(away, request)

print(json.dumps({"point": out.primary.point, "verdict": d.verdict.value,
                  "result": out.result_digest, "decision": d.decision_digest,
                  "entry": rec.entry_digest, "shadow_entry": shadow.entry_digest,
                  "blocked": [blocked.primary.point, db.verdict.value, blocked.result_digest,
                              db.decision_digest, rb.entry_digest],
                  "no_variation": [getattr(novar, "reason", None), dn.verdict.value,
                                   dn.decision_digest, rn.entry_digest],
                  "not_transported": [away.warnings[-1], dt.verdict.value,
                                      away.result_digest, dt.decision_digest,
                                      rt.entry_digest]}))
""".replace("TRUE_EFFECT", repr(TRUE_EFFECT))


def run(threads: int, hash_seed: str, defect: str = "") -> dict[str, object]:
    env = {
        **os.environ,
        "OMP_NUM_THREADS": str(threads),
        "OPENBLAS_NUM_THREADS": str(threads),
        "MKL_NUM_THREADS": str(threads),
        "PYTHONHASHSEED": hash_seed,
    }
    done = subprocess.run(
        [sys.executable, "-c", PROBE, defect], env=env, capture_output=True, text=True,
        check=False, timeout=600,
    )
    assert done.returncode == 0, done.stderr[-2000:]
    out: dict[str, object] = json.loads(done.stdout.strip().splitlines()[-1])
    return out


def digests(r: dict[str, object]) -> tuple[object, ...]:
    return r["result"], r["decision"], r["entry"], r["shadow_entry"], *new_paths(r)


def new_paths(r: dict[str, object]) -> tuple[object, ...]:
    """The digests of #107's paths: blocked/paired, NO_VARIATION, not transported."""
    b, nv, nt = r["blocked"], r["no_variation"], r["not_transported"]
    return (*b[2:], *nv[2:], *nt[2:])  # type: ignore[index]


#: (threads, hash seed): every thread count the bead names, each with a different seed,
#: plus the same thread count under two seeds.
RUNS = [(1, "1"), (2, "2"), (4, "3"), (8, "4"), (8, "0")]


@pytest.fixture(scope="module")
def pinned_runs() -> dict[tuple[int, str], dict[str, object]]:
    return {(t, s): run(t, s) for t, s in RUNS}


def test_the_same_decision_has_the_same_digests_at_every_thread_count(
    pinned_runs: dict[tuple[int, str], dict[str, object]],
) -> None:
    seen = {digests(r) for r in pinned_runs.values()}
    assert len(seen) == 1, {k: digests(r) for k, r in pinned_runs.items()}


def test_the_digested_decision_is_about_the_effect_the_model_sets(
    pinned_runs: dict[tuple[int, str], dict[str, object]],
) -> None:
    r = pinned_runs[RUNS[0]]
    assert abs(float(r["point"]) - TRUE_EFFECT) < 0.05  # type: ignore[arg-type]
    assert r["verdict"] == "act"


def test_the_new_paths_reproduce_and_are_about_the_answers_they_should_give(
    pinned_runs: dict[tuple[int, str], dict[str, object]],
) -> None:
    """#107's paths go through the same pinned ``analyze``: a paired design, a treatment
    that never varied, and an estimate declared for another population. Their digests are
    compared across every run by the first test; here each says what its design calls for.
    """
    r = pinned_runs[RUNS[0]]
    b, nv, nt = r["blocked"], r["no_variation"], r["not_transported"]
    assert abs(float(b[0]) - TRUE_EFFECT) < 0.15 and b[1] == "act"  # type: ignore[index]
    assert nv[0] == "NO_VARIATION" and nv[1] == "experiment"  # type: ignore[index]
    assert str(nt[0]).startswith("not transported") and nt[1] == "experiment"  # type: ignore[index]
    # Declaring a target population is digested: it is not the first analysis's result.
    assert nt[2] != r["result"]  # type: ignore[index]


def test_a_shadow_record_reproduces_too_and_its_tag_is_sealed(
    pinned_runs: dict[tuple[int, str], dict[str, object]],
) -> None:
    """The shadow tag (septa-95) is part of the entry digest, so a shadow record of the
    same decision has an entry digest of its own -- and reproduces like the real one
    (the first test compares it across every run). An absent tag is left out of the
    sealed record, so it cannot move a real record's digest."""
    r = pinned_runs[RUNS[0]]
    assert r["shadow_entry"] != r["entry"]


def test_negative_control_unpinned_blas_moves_the_digest_with_the_thread_count() -> None:
    """With BLAS left to the host's threads, 1 and 8 threads (same hash seed) disagree.

    This needs a BLAS that splits the probe's reductions across threads; OpenBLAS does
    at 50,000 rows. If a host's BLAS never threads them, this fails: the positive test
    could not have caught the defect there either.
    """
    one, eight = run(1, "0", "unpinned_blas"), run(8, "0", "unpinned_blas")
    assert digests(one) != digests(eight), "the host's BLAS gave the same bits at 1 and 8 threads"
    # #107's not-transported path re-digests the result after _analyze; the merge put it
    # inside the pin, and without the pin it moves too. (The paired and NO_VARIATION
    # paths do no thread-split reductions at this size: their digests are compared by
    # the first test, and the hash-seed control below covers the declared-target path.)
    assert one["not_transported"][2:] != eight["not_transported"][2:]  # type: ignore[index]
    # The defect is in the last bits, not the answer: the verdict does not move.
    assert one["verdict"] == eight["verdict"]


def test_negative_control_hash_ordered_components_move_the_digest_with_the_hash_seed() -> None:
    """With c-components in networkx's set order, hash seeds 0 and 1 disagree (same threads).

    String hashing under a fixed PYTHONHASHSEED is the same on every 64-bit host, so this
    control measures the same thing everywhere.
    """
    a, b = run(1, "0", "hash_ordered_components"), run(1, "1", "hash_ordered_components")
    assert a["result"] != b["result"]


def test_negative_control_hash_ordered_transport_note_moves_the_digest_with_the_hash_seed(
) -> None:
    """A not-transported warning that names its populations in set order: hash seeds 0
    and 1 disagree on that path's digests, so the first test does inspect #107's path."""
    a = run(1, "0", "hash_ordered_transport_note")
    b = run(1, "1", "hash_ordered_transport_note")
    assert a["not_transported"][2] != b["not_transported"][2]  # type: ignore[index]
    assert a["result"] == b["result"]  # the defect is confined to the declared-target path
