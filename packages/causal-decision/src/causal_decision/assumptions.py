"""The assumptions a verdict rested on, kept as versions, and what a revision does to them.

A decision's evidence is only as good as its causal graph, and until this module
the graph arrived inline with every request: nothing stored it, nothing
versioned it, and nothing could say which past verdicts a changed belief
affects. Here a causal model is a chain of immutable, content-addressed
versions -- the kernel's own :class:`CausalGraph` (every edge carrying its
origin and review status) plus the assumptions a graph cannot state.

Three operations change a model, and each produces a new version rather than
editing one:

* :func:`create` -- version 1.
* :func:`revise` -- a new graph or new assumptions. It may *propose* and
  *assert* edges, but it cannot approve or refute one: that is a review.
* :func:`review` -- approve or refute edges. **Nobody reviews an edge they
  introduced.** Every version remembers who added each edge, so the rule holds
  across versions, not just within one call. Approval is not something an author
  can grant themselves -- the same principle the decision layer applies to
  actions.

And two read one:

* :func:`diff` -- what changed, including the edges that went away. An absent
  edge is the strongest assumption a graph makes, so a diff that lost one would
  hide the change that matters most.
* :func:`impact` -- if the *new* graph is true, is the estimate a verdict made
  under the old one still an estimate of the effect it claimed? For back-door
  identification that has an exact answer: the OLD adjustment set must be a
  valid adjustment set under the NEW graph. That is neither "the kernel still
  picks back-door" nor "the kernel's new set is valid" -- both easy, plausible,
  and wrong, and both seeded defects in the pre-registered A0 gate
  (``benchmarks/a0-assumption-impact``).

Everything here is pure: no clock, no store. The caller supplies timestamps and
identities, so a model's history can be rebuilt from its records and re-verified
anywhere.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Sequence
from enum import StrEnum
from typing import Literal

from causal_core.canonical import digest
from causal_core.contract import (
    CausalGraph,
    CausalQuery,
    Edge,
    EdgeStatus,
    IdentificationCertificate,
    IdentificationStatus,
    Strategy,
)
from causal_core.graph import Graph, as_causal_graph
from causal_core.identification import (
    IdentificationError,
    InvalidAdjustmentSet,
    adjustment_checks,
    identify,
)
from pydantic import BaseModel, ConfigDict, Field


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


_NAME = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")


class AssumptionKind(StrEnum):
    """Assumptions a causal graph cannot express, but an estimate still rests on."""

    no_interference = "no_interference"  # one unit's treatment does not move another's outcome
    consistency = "consistency"  # the treatment as recorded is the treatment as it would be applied
    positivity = "positivity"  # every kind of unit could have received either arm
    measurement = "measurement"  # the variables measure what the graph says they are
    stable_population = "stable_population"  # the future population resembles the analysed one
    timing = "timing"  # causes precede effects in the data as recorded
    other = "other"


class Assumption(_Model):
    kind: AssumptionKind
    statement: str = Field(min_length=10)
    about: tuple[str, ...] = Field(default=(), description="Variables the assumption concerns.")


class EdgeReview(_Model):
    """One reviewer's ruling on one edge."""

    edge: str = Field(description='The edge as labelled by `edge_key`, e.g. "load -> minutes".')
    status: Literal["approved", "refuted"]
    reason: str = Field(min_length=5)


class ModelRef(_Model):
    """How a decision names the model it rested on."""

    name: str
    version: int
    digest: str


class ModelVersion(_Model):
    """One immutable version. ``digest`` covers every other field, including the parent link."""

    name: str
    version: int = Field(ge=1)
    graph: CausalGraph
    assumptions: tuple[Assumption, ...] = ()
    parent_digest: str | None = None
    author_id: str = Field(min_length=1)
    created_at: str = Field(description="ISO 8601, from the caller's clock.")
    why: str = Field(min_length=5)
    edge_authors: dict[str, str] = Field(
        description="Who introduced each edge (edge_key -> author id). Carried across versions, "
        "so the no-self-review rule sees an edge's origin however long ago it was added."
    )
    reviews: tuple[EdgeReview, ...] = Field(
        default=(), description="The rulings that produced this version from its parent."
    )
    digest: str = ""

    def content(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"digest"})

    def sealed(self) -> ModelVersion:
        return self.model_copy(update={"digest": digest(self.content())})

    def ref(self) -> ModelRef:
        return ModelRef(name=self.name, version=self.version, digest=self.digest)

    @property
    def unreviewed_edges(self) -> list[str]:
        return [edge_key(e) for e in self.graph.edges if e.status is EdgeStatus.proposed]


class ModelError(ValueError):
    """A change the model's rules do not allow."""


class SelfReviewError(ModelError):
    """A reviewer ruled on an edge they introduced."""


# ── building versions ─────────────────────────────────────────────────────────


def edge_key(e: Edge) -> str:
    """An edge's identity: its endpoints and kind. A bidirected edge has no direction."""
    if e.kind == "bidirected":
        a, b = sorted((e.source, e.target))
        return f"{a} <-> {b}"
    return f"{e.source} -> {e.target}"


def canonical(graph: CausalGraph | str) -> CausalGraph:
    """The same graph, written the same way, so that equal graphs have equal digests.

    Validated on the way in (acyclic, no self-loops, no duplicate edges): a model
    that the kernel cannot analyse is not one to store.
    """
    spec = as_causal_graph(graph)
    Graph.from_spec(spec)
    keys = Counter(edge_key(e) for e in spec.edges)
    dupes = sorted(k for k, n in keys.items() if n > 1)
    if dupes:
        raise ModelError(f"the graph states these edges more than once: {dupes}")
    edges = tuple(
        e if e.kind == "directed" or e.source <= e.target
        else e.model_copy(update={"source": e.target, "target": e.source})
        for e in spec.edges
    )
    return CausalGraph(
        nodes=tuple(sorted(set(spec.nodes))),
        edges=tuple(sorted(edges, key=edge_key)),
        latent=tuple(sorted(set(spec.latent))),
    )


def _check_name(name: str) -> None:
    if not _NAME.match(name):
        raise ModelError(
            f"model name {name!r} must be lowercase letters, digits, '.', '_' or '-' "
            "(1-128 characters, starting with a letter or digit)"
        )


def create(
    name: str,
    graph: CausalGraph | str,
    *,
    author_id: str,
    created_at: str,
    why: str,
    assumptions: Sequence[Assumption] = (),
) -> ModelVersion:
    """Version 1 of a model."""
    _check_name(name)
    spec = canonical(graph)
    _refuse_rulings(spec, previous=None)
    return ModelVersion(
        name=name,
        version=1,
        graph=spec,
        assumptions=tuple(assumptions),
        author_id=author_id,
        created_at=created_at,
        why=why,
        edge_authors={edge_key(e): author_id for e in spec.edges},
    ).sealed()


def revise(
    parent: ModelVersion,
    *,
    author_id: str,
    created_at: str,
    why: str,
    graph: CausalGraph | str | None = None,
    assumptions: Sequence[Assumption] | None = None,
) -> ModelVersion:
    """A new version with a new graph and/or new assumptions.

    Edges that persist keep their author; new ones are attributed to ``author_id``.
    A revision cannot rule on an edge: setting one to ``approved`` or ``refuted``
    is a review, and a revision that tried would let an author approve their own
    edge by re-submitting the graph.
    """
    spec = parent.graph if graph is None else canonical(graph)
    _refuse_rulings(spec, previous=parent.graph)
    authors = {
        edge_key(e): parent.edge_authors.get(edge_key(e), author_id) for e in spec.edges
    }
    if spec == parent.graph and (assumptions is None or tuple(assumptions) == parent.assumptions):
        raise ModelError("a revision must change the graph or the assumptions")
    return ModelVersion(
        name=parent.name,
        version=parent.version + 1,
        graph=spec,
        assumptions=parent.assumptions if assumptions is None else tuple(assumptions),
        parent_digest=parent.digest,
        author_id=author_id,
        created_at=created_at,
        why=why,
        edge_authors=authors,
    ).sealed()


def review(
    parent: ModelVersion,
    reviews: Sequence[EdgeReview],
    *,
    reviewer_id: str,
    created_at: str,
) -> ModelVersion:
    """Approve or refute edges, as ``reviewer_id``. One reviewer per version.

    A refuted edge stays in the graph with status ``refuted`` -- the kernel drops it
    before analysis -- so the record keeps what was believed and then rejected.
    """
    if not reviews:
        raise ModelError("a review must rule on at least one edge")
    by_key = {edge_key(e): e for e in parent.graph.edges}
    seen: set[str] = set()
    updated: dict[str, Edge] = {}
    for r in reviews:
        if r.edge in seen:
            raise ModelError(f"edge {r.edge!r} is ruled on twice in one review")
        seen.add(r.edge)
        edge = by_key.get(r.edge)
        if edge is None:
            raise ModelError(f"edge {r.edge!r} is not in {parent.name} v{parent.version}")
        author = parent.edge_authors.get(r.edge)
        if author == reviewer_id:
            raise SelfReviewError(
                f"{reviewer_id!r} introduced {r.edge!r} and so cannot review it"
            )
        updated[r.edge] = edge.model_copy(update={"status": EdgeStatus(r.status)})
    edges = tuple(updated.get(edge_key(e), e) for e in parent.graph.edges)
    spec = parent.graph.model_copy(update={"edges": edges})
    summary = "; ".join(f"{r.status} {r.edge}: {r.reason}" for r in reviews)
    return ModelVersion(
        name=parent.name,
        version=parent.version + 1,
        graph=spec,
        assumptions=parent.assumptions,
        parent_digest=parent.digest,
        author_id=reviewer_id,
        created_at=created_at,
        why=f"review: {summary}"[:2000],
        edge_authors=dict(parent.edge_authors),
        reviews=tuple(reviews),
    ).sealed()


def _refuse_rulings(spec: CausalGraph, previous: CausalGraph | None) -> None:
    before = {} if previous is None else {edge_key(e): e.status for e in previous.edges}
    ruled = (EdgeStatus.approved, EdgeStatus.refuted)
    offending = sorted(
        edge_key(e)
        for e in spec.edges
        if e.status in ruled and before.get(edge_key(e)) is not e.status
    )
    if offending:
        raise ModelError(
            f"edges {offending} arrive already {'/'.join(s.value for s in ruled)}: rulings are "
            "made by review(), by someone other than the edge's author"
        )


# ── the history holds ─────────────────────────────────────────────────────────


class HistoryReport(_Model):
    ok: bool
    length: int
    head_digest: str | None = None
    broken_at: int | None = None
    reason: str = ""


def verify_history(versions: Sequence[ModelVersion]) -> HistoryReport:
    """Recompute every digest and link. Like the decision chain, this detects; it cannot prevent."""
    prev: ModelVersion | None = None
    for i, v in enumerate(versions):
        reason = ""
        if v.sealed().digest != v.digest:
            reason = "the stored digest does not match the version's content"
        elif prev is None and (v.version != 1 or v.parent_digest is not None):
            reason = "the first version must be version 1 with no parent"
        elif prev is not None and v.name != prev.name:
            reason = f"name changes from {prev.name!r} to {v.name!r}"
        elif prev is not None and v.version != prev.version + 1:
            reason = f"version {v.version} follows {prev.version}"
        elif prev is not None and v.parent_digest != prev.digest:
            reason = "the parent link does not point at the previous version"
        if reason:
            return HistoryReport(ok=False, length=len(versions), broken_at=i, reason=reason)
        prev = v
    return HistoryReport(
        ok=True, length=len(versions), head_digest=None if prev is None else prev.digest
    )


# ── what changed ──────────────────────────────────────────────────────────────


class EdgeChange(_Model):
    edge: str
    before: Edge
    after: Edge


class ModelDiff(_Model):
    added_edges: tuple[Edge, ...] = ()
    removed_edges: tuple[Edge, ...] = Field(
        default=(),
        description="Edges the new graph no longer has. Each is a NEW assumption: that the "
        "effect it described does not exist.",
    )
    changed_edges: tuple[EdgeChange, ...] = Field(
        default=(), description="Same endpoints and kind; origin, status or evidence changed."
    )
    added_nodes: tuple[str, ...] = ()
    removed_nodes: tuple[str, ...] = ()
    latent_added: tuple[str, ...] = ()
    latent_removed: tuple[str, ...] = ()
    assumptions_added: tuple[Assumption, ...] = ()
    assumptions_removed: tuple[Assumption, ...] = ()

    @property
    def empty(self) -> bool:
        return not any(getattr(self, f) for f in type(self).model_fields)


def diff(old: ModelVersion | CausalGraph, new: ModelVersion | CausalGraph) -> ModelDiff:
    a, b = _graph_of(old), _graph_of(new)
    ea = {edge_key(e): e for e in a.edges}
    eb = {edge_key(e): e for e in b.edges}
    aa = old.assumptions if isinstance(old, ModelVersion) else ()
    ab = new.assumptions if isinstance(new, ModelVersion) else ()
    return ModelDiff(
        added_edges=tuple(eb[k] for k in sorted(eb.keys() - ea.keys())),
        removed_edges=tuple(ea[k] for k in sorted(ea.keys() - eb.keys())),
        changed_edges=tuple(
            EdgeChange(edge=k, before=ea[k], after=eb[k])
            for k in sorted(ea.keys() & eb.keys())
            if ea[k] != eb[k]
        ),
        added_nodes=tuple(sorted(set(b.nodes) - set(a.nodes))),
        removed_nodes=tuple(sorted(set(a.nodes) - set(b.nodes))),
        latent_added=tuple(sorted(set(b.latent) - set(a.latent))),
        latent_removed=tuple(sorted(set(a.latent) - set(b.latent))),
        assumptions_added=tuple(x for x in ab if x not in aa),
        assumptions_removed=tuple(x for x in aa if x not in ab),
    )


def apply_diff(old: ModelVersion | CausalGraph, d: ModelDiff) -> CausalGraph:
    """``old`` with ``d`` applied. ``canonical(apply_diff(a, diff(a, b))) == canonical(b)``."""
    a = _graph_of(old)
    edges = {edge_key(e): e for e in a.edges}
    for e in d.removed_edges:
        edges.pop(edge_key(e), None)
    for c in d.changed_edges:
        edges[c.edge] = c.after
    for e in d.added_edges:
        edges[edge_key(e)] = e
    nodes = (set(a.nodes) - set(d.removed_nodes)) | set(d.added_nodes)
    latent = (set(a.latent) - set(d.latent_removed)) | set(d.latent_added)
    return CausalGraph(
        nodes=tuple(sorted(nodes)),
        edges=tuple(sorted(edges.values(), key=edge_key)),
        latent=tuple(sorted(latent)),
    )


# ── what a revision does to a verdict ─────────────────────────────────────────


class ImpactStatus(StrEnum):
    holds = "holds"  # the old estimate is still valid if the new graph is true
    invalidated = "invalidated"  # it is not: re-analyse before relying on the verdict
    unverified = "unverified"  # a strategy this function cannot check: re-analyse
    gained = "gained"  # the old graph could not identify the effect; the new one can
    still_unidentified = "still_unidentified"


class Impact(_Model):
    status: ImpactStatus
    detail: str
    old_strategy: Strategy | None = None
    new_strategy: Strategy | None = None
    old_adjustment_set: tuple[str, ...] = ()
    new_adjustment_set: tuple[str, ...] = ()
    unreviewed_before: tuple[str, ...] = ()
    unreviewed_after: tuple[str, ...] = ()
    digest: str = ""

    @property
    def reanalysis_required(self) -> bool:
        return self.status is not ImpactStatus.holds


def _graph_of(m: ModelVersion | CausalGraph | str) -> CausalGraph:
    return m.graph if isinstance(m, ModelVersion) else as_causal_graph(m)


def _certify(g: Graph, query: CausalQuery) -> tuple[IdentificationCertificate | None, str]:
    """The kernel's certificate, or None and why the kernel could not produce one."""
    try:
        return identify(g, query), ""
    except (IdentificationError, InvalidAdjustmentSet) as exc:
        return None, str(exc)


def _unmeasured(spec: CausalGraph, unmeasured: Iterable[str]) -> CausalGraph:
    """The graph as the analysis saw it: nodes the data lacked are latent (as the kernel does)."""
    missing = sorted(set(unmeasured) & set(spec.nodes) - set(spec.latent))
    if not missing:
        return spec
    return spec.model_copy(update={"latent": tuple(sorted(set(spec.latent) | set(missing)))})


def impact(
    old: ModelVersion | CausalGraph | str,
    new: ModelVersion | CausalGraph | str,
    query: CausalQuery,
    *,
    unmeasured: Iterable[str] = (),
) -> Impact:
    """If ``new`` is the true graph, does the estimate made under ``old`` still stand?

    ``query`` is the decision's own query. For a recorded decision, pin
    ``adjust_for`` to the set its analysis actually used, so that set -- not whatever
    the kernel would choose today -- is what gets judged. ``unmeasured`` names the
    graph nodes the data had no column for; the kernel treated them as latent, and
    so does this.
    """
    gaps = tuple(unmeasured)
    g_old = Graph.from_spec(_unmeasured(_graph_of(old), gaps))
    g_new = Graph.from_spec(_unmeasured(_graph_of(new), gaps))
    before = tuple(g_old.unreviewed_edges())
    after = tuple(g_new.unreviewed_edges())
    x, y = query.treatment, query.outcome

    def done(status: ImpactStatus, detail: str, **kw: object) -> Impact:
        out = Impact(
            status=status, detail=detail, unreviewed_before=before, unreviewed_after=after, **kw
        )
        return out.model_copy(update={"digest": digest(out.model_dump(mode="json"))})

    cert_old, why_old = _certify(g_old, query)
    # What the kernel would choose under the new graph, free of any pinned set: the old
    # set is judged separately below, and reporting "the pinned set is invalid" as the
    # new certificate would say nothing about what to use instead.
    cert_new, why_new = _certify(g_new, query.model_copy(update={"adjust_for": None}))
    new_kw: dict[str, object] = {}
    if cert_new is not None and cert_new.status is IdentificationStatus.identified:
        new_kw = {
            "new_strategy": cert_new.strategy,
            "new_adjustment_set": cert_new.adjustment_set,
        }

    if cert_old is None or cert_old.status is not IdentificationStatus.identified:
        if new_kw:
            return done(
                ImpactStatus.gained,
                "the old graph did not identify the effect"
                + (f" ({why_old})" if why_old else "")
                + "; the new one does, so the question can now be analysed",
                **new_kw,
            )
        return done(
            ImpactStatus.still_unidentified,
            why_new or "neither graph identifies the effect",
        )

    strategy = cert_old.strategy
    zset = tuple(cert_old.adjustment_set)
    old_kw: dict[str, object] = {"old_strategy": strategy, "old_adjustment_set": zset}

    missing = sorted({x, y} - g_new.observed)
    if missing:
        return done(
            ImpactStatus.invalidated,
            f"{missing} are no longer observed variables in the new graph",
            **old_kw,
            **new_kw,
        )

    if strategy is Strategy.randomized:
        if new_kw.get("new_strategy") is Strategy.randomized:
            return done(ImpactStatus.holds, "the design still randomizes the treatment", **old_kw,
                        **new_kw)
        return done(
            ImpactStatus.invalidated,
            "the new graph gives the treatment causes, which contradicts the randomized design "
            f"the estimate relied on: {why_new or 'identification changed'}",
            **old_kw,
            **new_kw,
        )

    if strategy is Strategy.backdoor:
        unobserved = sorted(set(zset) - g_new.observed)
        if unobserved:
            return done(
                ImpactStatus.invalidated,
                f"the estimate adjusted for {unobserved}, which the new graph makes latent or "
                "removes",
                **old_kw,
                **new_kw,
            )
        failing = [c for c in adjustment_checks(g_new, x, y, zset) if not c.holds]
        if failing:
            reasons = "; ".join(c.detail or c.name for c in failing)
            return done(
                ImpactStatus.invalidated,
                f"the adjustment set the estimate used, {{{', '.join(zset)}}}, is not valid under "
                f"the new graph: {reasons}",
                **old_kw,
                **new_kw,
            )
        return done(
            ImpactStatus.holds,
            f"the adjustment set the estimate used, {{{', '.join(zset)}}}, is still valid under "
            "the new graph",
            **old_kw,
            **new_kw,
        )

    # Front-door, IV, proximal, general ID: holds only on an identical certificate.
    if cert_new is None or not new_kw:
        return done(
            ImpactStatus.invalidated,
            f"the new graph does not identify the effect: {why_new or 'not identified'}",
            **old_kw,
        )
    same = _elements(cert_old) == _elements(cert_new)
    if same:
        return done(
            ImpactStatus.holds,
            f"the new graph identifies the effect by the same {strategy} certificate",
            **old_kw,
            **new_kw,
        )
    return done(
        ImpactStatus.unverified,
        f"identification by {strategy} cannot be re-checked piecewise, and the new graph's "
        "certificate differs: re-analyse",
        **old_kw,
        **new_kw,
    )


def _elements(cert: IdentificationCertificate) -> tuple[object, ...]:
    return tuple(
        getattr(cert, f)
        for f in ("strategy", "tier", "adjustment_set", "instruments", "mediators",
                  "treatment_proxies", "outcome_proxies")
    )


def impacts(
    old: ModelVersion | CausalGraph | str,
    new: ModelVersion | CausalGraph | str,
    queries: Iterable[CausalQuery],
) -> list[Impact]:
    return [impact(old, new, q) for q in queries]
