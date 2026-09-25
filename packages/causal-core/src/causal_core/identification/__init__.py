"""
Identification: decide *whether* and *how* an effect can be computed from data.

:func:`identify` returns an :class:`IdentificationCertificate`. The strategy is
chosen in this order, and every strategy that claims nonparametric
identification is cross-checked against the complete ID algorithm:

1. ``randomized`` -- the query declares randomization and the graph agrees
   (no arrows or unmeasured confounding into the treatment).
2. ``backdoor``   -- a valid adjustment set exists (generalized adjustment
   criterion, Perković et al. 2018). The efficiency-optimal O-set (Henckel,
   Perković & Maathuis 2022) is preferred; otherwise a minimal set.
3. ``frontdoor``  -- Pearl's front-door criterion on the mediators.
4. ``id_general`` -- identified by ID but by neither of the above.
5. Only if the effect is *not* nonparametrically identified: ``iv`` (a
   conditional instrument) or ``proximal`` (declared proxies), both tier B
   because they additionally need parametric assumptions the graph cannot state.

A user-supplied adjustment set is validated, never silently replaced: if it is
invalid, :class:`InvalidAdjustmentSet` explains which condition fails and
suggests a valid set when one exists.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from itertools import islice, pairwise

import networkx as nx

from causal_core.canonical import digest
from causal_core.contract import (
    CausalQuery,
    ConditionCheck,
    Design,
    IdentificationCertificate,
    IdentificationStatus,
    Strategy,
    Tier,
)
from causal_core.graph import Graph
from causal_core.identification.id_algorithm import ADMG, IDResult, identify_id

ENGINE = "causal_core.identification/0.2"


class IdentificationError(ValueError):
    """The query cannot be posed against this graph (unknown or latent variables)."""


class InvalidAdjustmentSet(ValueError):
    def __init__(self, detail: str, suggestion: tuple[str, ...] | None) -> None:
        super().__init__(detail)
        self.detail = detail
        self.suggestion = suggestion


class IdentificationInconsistency(RuntimeError):
    """Two identification engines disagree. Always a bug; never a user error."""


# ── helpers ───────────────────────────────────────────────────────────────────


def _admg(g: Graph) -> ADMG:
    directed, bidirected = g.admg
    return ADMG.from_edges(g.observed, directed.edges, [tuple(p) for p in bidirected])


def _fmt(vs: Iterable[str]) -> str:
    return ", ".join(sorted(vs))


def open_path(
    g: Graph, x: str, y: str, z: Iterable[str], dag: nx.DiGraph, limit: int = 5000
) -> list[str] | None:
    """Return one path between x and y that is d-connected given z, or None."""
    zs = set(z)
    an_z = g.ancestors(zs, dag) if zs else set()
    skel = dag.to_undirected(as_view=True)
    for path in islice(nx.all_simple_paths(skel, x, y), limit):
        ok = True
        for i in range(1, len(path) - 1):
            a, b, c = path[i - 1], path[i], path[i + 1]
            collider = dag.has_edge(a, b) and dag.has_edge(c, b)
            if (collider and b not in an_z) or (not collider and b in zs):
                ok = False
                break
        if ok:
            return list(path)
    return None


def _render_path(dag: nx.DiGraph, path: Sequence[str]) -> str:
    out = [path[0]]
    for a, b in pairwise(path):
        out.append("->" if dag.has_edge(a, b) else "<-")
        out.append(b)
    return " ".join(out)


# ── back-door / generalized adjustment ───────────────────────────────────────


def adjustment_checks(g: Graph, x: str, y: str, zset: Iterable[str]) -> list[ConditionCheck]:
    """The two conditions of the generalized adjustment criterion, with reasons."""
    z = set(zset)
    forb = g.forbidden({x}, {y})
    bad = sorted(z & forb)
    latent = sorted(z & g.latent)
    pbd = g.proper_backdoor_dag({x}, {y})
    checks = [
        ConditionCheck(
            name="adjustment set is observed",
            holds=not latent,
            detail=f"latent variables cannot be adjusted for: {latent}" if latent else "",
        ),
        ConditionCheck(
            name="no adjustment variable is a descendant of a causal node",
            holds=not bad,
            detail=(
                f"{bad} lie on or downstream of a causal path from {x} to {y} "
                "(mediators or their descendants); adjusting for them biases the total effect"
                if bad
                else ""
            ),
        ),
    ]
    blocked = g.d_separated({x}, {y}, z, pbd)
    detail = ""
    if not blocked:
        path = open_path(g, x, y, z, pbd)
        if path:
            detail = f"non-causal path left open: {_render_path(pbd, path)}"
    checks.append(
        ConditionCheck(
            name="all non-causal paths are blocked",
            holds=blocked,
            detail=detail,
        )
    )
    return checks


def find_adjustment_set(g: Graph, x: str, y: str) -> tuple[str, ...] | None:
    """O-set if it is observed and valid; otherwise a minimal valid observed set."""
    forb = g.forbidden({x}, {y})
    cn = g.causal_nodes({x}, {y})
    o_set = (g.parents(cn) - forb) if cn else set()
    if cn and not (o_set & g.latent) and all(c.holds for c in adjustment_checks(g, x, y, o_set)):
        return tuple(sorted(o_set))
    restricted = (g.observed - forb) - {x, y}
    pbd = g.proper_backdoor_dag({x}, {y})
    sep = nx.find_minimal_d_separator(pbd, {x}, {y}, restricted=restricted)
    if sep is None:
        return None
    return tuple(sorted(sep))


# ── front-door ────────────────────────────────────────────────────────────────


def frontdoor_checks(g: Graph, x: str, y: str, m: set[str]) -> list[ConditionCheck]:
    dag_no_m = g.dag.copy()
    dag_no_m.remove_nodes_from(m)
    intercepts = y not in nx.descendants(dag_no_m, x)
    no_bd_xm = g.d_separated({x}, m, (), g.without_outgoing({x}))
    bd_my_blocked = g.d_separated(m, {y}, {x}, g.without_outgoing(m))
    return [
        ConditionCheck(name="mediators intercept every directed path", holds=intercepts),
        ConditionCheck(name="no back-door path from treatment to mediators", holds=no_bd_xm),
        ConditionCheck(
            name="treatment blocks every back-door path from mediators to outcome",
            holds=bd_my_blocked,
        ),
    ]


def find_frontdoor(g: Graph, x: str, y: str) -> tuple[tuple[str, ...], list[ConditionCheck]] | None:
    cn = g.causal_nodes({x}, {y}) - {y}
    candidates: list[set[str]] = []
    for m in (cn, g.children({x}) & cn):
        if m and not (m & g.latent) and m not in candidates:
            candidates.append(set(m))
    for m in candidates:
        checks = frontdoor_checks(g, x, y, m)
        if all(c.holds for c in checks):
            return tuple(sorted(m)), checks
    return None


# ── instruments ───────────────────────────────────────────────────────────────


def find_instrument(
    g: Graph, x: str, y: str, declared: Sequence[str] | None
) -> tuple[str, tuple[str, ...], list[ConditionCheck]] | None:
    """A conditional instrument (Z, W) for the total effect of x on y."""
    de_x = g.descendants({x})
    g_x_out = g.without_outgoing({x})
    cands = list(declared) if declared else sorted(g.observed - {x, y} - de_x)
    for z in cands:
        if z not in g.observed or z in de_x:
            continue
        restricted = g.observed - de_x - {z, x, y}
        conds: list[set[str]] = [set()]
        sep = nx.find_minimal_d_separator(g_x_out, {z}, {y}, restricted=restricted)
        if sep is not None and sep:
            conds.append(set(sep))
        for w in conds:
            exclusion = g.d_separated({z}, {y}, w, g_x_out)
            relevance = not g.d_separated({z}, {x}, w)
            if exclusion and relevance:
                checks = [
                    ConditionCheck(name="instrument is not affected by treatment", holds=True),
                    ConditionCheck(
                        name="instrument is associated with treatment (graphical relevance)",
                        holds=True,
                        detail="strength is tested on data (first-stage F)",
                    ),
                    ConditionCheck(
                        name="instrument affects outcome only through treatment, and shares "
                        "no unmeasured cause with it",
                        holds=True,
                        detail=f"given {{{_fmt(w)}}}" if w else "",
                    ),
                ]
                return z, tuple(sorted(w)), checks
    return None


# ── proximal ──────────────────────────────────────────────────────────────────


def proximal_checks(
    g: Graph, x: str, y: str, zs: Sequence[str], ws: Sequence[str]
) -> tuple[tuple[str, ...], list[ConditionCheck]]:
    de_x = g.descendants({x})
    u = {n for n in g.latent if n in g.ancestors({x}) or n in g.ancestors({y})}
    covs = (g.parents({x, y}) & g.observed) - {x, y} - set(zs) - set(ws) - de_x
    z, w = set(zs), set(ws)
    checks = [
        ConditionCheck(
            name="proxies are observed and not affected by treatment",
            holds=(z | w) <= g.observed and not ((z | w) & de_x),
        ),
        ConditionCheck(
            name="at least as many treatment proxies as outcome proxies",
            holds=len(z) >= len(w) > 0,
        ),
        ConditionCheck(
            name="treatment proxies are independent of the outcome given treatment, "
            "unmeasured confounders and covariates",
            holds=g.d_separated(z, {y}, {x} | u | covs),
        ),
        ConditionCheck(
            name="outcome proxies are independent of treatment and treatment proxies given "
            "unmeasured confounders and covariates",
            holds=g.d_separated(w, {x} | z, u | covs),
        ),
        ConditionCheck(
            name="unmeasured confounders and covariates block every back-door path",
            holds=g.d_separated({x}, {y}, u | covs, g.proper_backdoor_dag({x}, {y})),
        ),
    ]
    return tuple(sorted(covs)), checks


# ── the entry point ───────────────────────────────────────────────────────────

_BACKDOOR_ASSUMPTIONS = (
    "causal sufficiency relative to the graph: every absent edge and absent <-> is a claim",
    "positivity: every adjustment stratum has a non-zero chance of each treatment level",
    "consistency and no interference between units (SUTVA)",
)


def identify(g: Graph, query: CausalQuery) -> IdentificationCertificate:
    x, y = query.treatment, query.outcome
    for role, v in (("treatment", x), ("outcome", y)):
        if v not in g.observed:
            where = "latent" if v in g.latent else "absent from the graph"
            raise IdentificationError(f"{role} {v!r} is {where}")
    if x == y:
        raise IdentificationError("treatment and outcome must differ")

    gdig = digest(g.spec)
    general: IDResult = identify_id(_admg(g), {x}, {y})
    unreviewed = tuple(g.unreviewed_edges())
    base = {
        "engine": ENGINE,
        "graph_digest": gdig,
        "general_id": general.expression,
        "unreviewed_edges": unreviewed,
    }

    def certify(**kw: object) -> IdentificationCertificate:
        cert = IdentificationCertificate(**{**base, **kw})
        nonparametric = cert.tier is Tier.A and cert.status is IdentificationStatus.identified
        if nonparametric and not general.identified:
            raise IdentificationInconsistency(
                f"{cert.strategy} claims identification but ID fails: {general.expression}"
            )
        return cert

    # A panel design is identified by assumptions outside the graph (parallel trends,
    # no anticipation): tier C, and the graph's back-door structure is not what the
    # estimate rests on. Imported here to keep panel -> identification one-way.
    if query.design is Design.difference_in_differences:
        from causal_core.panel import certificate as panel_certificate

        return panel_certificate(gdig, ENGINE)

    # A user-chosen adjustment set is validated, not replaced.
    if query.adjust_for is not None:
        z = set(query.adjust_for)
        unknown = z - g.observed - g.latent
        if unknown:
            raise IdentificationError(f"adjust_for names unknown variables: {sorted(unknown)}")
        checks = adjustment_checks(g, x, y, z)
        if not all(c.holds for c in checks):
            reasons = "; ".join(c.detail or c.name for c in checks if not c.holds)
            raise InvalidAdjustmentSet(
                f"{{{_fmt(z)}}} is not a valid adjustment set for {x} -> {y}: {reasons}",
                find_adjustment_set(g, x, y),
            )
        strategy = Strategy.backdoor
        if query.design is Design.randomized and not g.parents({x}):
            strategy = Strategy.randomized
        return certify(
            status=IdentificationStatus.identified,
            strategy=strategy,
            tier=Tier.A,
            estimand=_backdoor_estimand(x, y, tuple(sorted(z))),
            adjustment_set=tuple(sorted(z)),
            conditions=tuple(checks),
            assumptions=_BACKDOOR_ASSUMPTIONS,
        )

    conditions: list[ConditionCheck] = []
    if query.design is Design.randomized:
        confounded = sorted(g.parents({x}))
        consistent = not confounded
        conditions.append(
            ConditionCheck(
                name="graph is consistent with randomized treatment",
                holds=consistent,
                detail="" if consistent else f"graph has causes of the treatment: {confounded}",
            )
        )
        if consistent:
            return certify(
                status=IdentificationStatus.identified,
                strategy=Strategy.randomized,
                tier=Tier.A,
                estimand=f"E[{y} | do({x}=t)] = E[{y} | {x}=t]",
                conditions=tuple(conditions),
                assumptions=(
                    "treatment assigned independently of potential outcomes (randomization)",
                    "consistency and no interference between units (SUTVA)",
                ),
            )

    adj = find_adjustment_set(g, x, y)
    if adj is not None:
        conditions.extend(adjustment_checks(g, x, y, adj))
        return certify(
            status=IdentificationStatus.identified,
            strategy=Strategy.backdoor,
            tier=Tier.A,
            estimand=_backdoor_estimand(x, y, adj),
            adjustment_set=adj,
            conditions=tuple(conditions),
            assumptions=_BACKDOOR_ASSUMPTIONS,
        )

    fd = find_frontdoor(g, x, y)
    if fd is not None:
        mediators, checks = fd
        conditions.extend(checks)
        m = _fmt(mediators)
        return certify(
            status=IdentificationStatus.identified,
            strategy=Strategy.frontdoor,
            tier=Tier.A,
            estimand=f"Σ_{{{m}}} P({m} | {x}) Σ_{{{x}'}} P({y} | {m}, {x}') P({x}')",
            mediators=mediators,
            conditions=tuple(conditions),
            assumptions=(
                "the mediators capture every causal path from treatment to outcome",
                "positivity of the mediators given treatment",
                "consistency and no interference between units (SUTVA)",
            ),
        )

    if general.identified:
        return certify(
            status=IdentificationStatus.identified,
            strategy=Strategy.id_general,
            tier=Tier.A,
            estimand=general.expression,
            conditions=tuple(conditions),
            assumptions=_BACKDOOR_ASSUMPTIONS[:1] + _BACKDOOR_ASSUMPTIONS[2:],
        )

    # Not nonparametrically identified. Parametric routes, if the data allow.
    if query.treatment_proxies or query.outcome_proxies:
        covs, checks = proximal_checks(g, x, y, query.treatment_proxies, query.outcome_proxies)
        conditions.extend(checks)
        if all(c.holds for c in checks):
            zp, wp = _fmt(query.treatment_proxies), _fmt(query.outcome_proxies)
            return certify(
                status=IdentificationStatus.identified,
                strategy=Strategy.proximal,
                tier=Tier.B,
                estimand=f"θ in E[{y} | {x}, {zp}, C] = E[b({wp}, {x}, C) | {x}, {zp}, C] with "
                f"b linear in {x}; C = {{{_fmt(covs)}}}",
                treatment_proxies=tuple(sorted(query.treatment_proxies)),
                outcome_proxies=tuple(sorted(query.outcome_proxies)),
                adjustment_set=covs,
                conditions=tuple(conditions),
                assumptions=(
                    "proxy conditions as encoded in the graph",
                    "completeness: the proxies are rich enough to recover the confounder",
                    "a linear outcome bridge function (constant effect)",
                ),
            )

    iv = find_instrument(g, x, y, query.instruments)
    if iv is not None:
        instrument, w, checks = iv
        conditions.extend(checks)
        wtxt = f" | {_fmt(w)}" if w else ""
        return certify(
            status=IdentificationStatus.identified,
            strategy=Strategy.iv,
            tier=Tier.B,
            estimand=f"Cov({y}, {instrument}{wtxt}) / Cov({x}, {instrument}{wtxt})",
            instruments=(instrument,),
            adjustment_set=w,
            conditions=tuple(conditions),
            assumptions=(
                "exclusion and instrument independence as encoded in the graph",
                "effect homogeneity (or linear structural equations): without it the IV "
                "estimand is a weighted local effect, not the ATE",
                "consistency and no interference between units (SUTVA)",
            ),
        )

    return certify(
        status=IdentificationStatus.not_identified,
        strategy=None,
        tier=Tier.D,
        estimand="not identified",
        conditions=tuple(conditions),
    )


def missing_information(g: Graph, x: str, y: str) -> tuple[str, ...]:
    """What would make a non-identified effect identifiable."""
    _directed, bidirected = g.admg
    confounded = sorted("<->".join(sorted(p)) for p in bidirected if x in p or y in p)
    hints = []
    if confounded:
        hints.append("measure the unobserved common cause(s) behind: " + ", ".join(confounded))
    hints.extend(
        [
            f"declare an instrument: a variable that moves {x}, affects {y} only through {x}, "
            f"and shares no unmeasured cause with {y}",
            "declare treatment and outcome proxies of the unmeasured confounder "
            "(proximal inference)",
            f"randomize {x} (an experiment identifies the effect by design)",
        ]
    )
    return tuple(hints)


def _backdoor_estimand(x: str, y: str, z: Sequence[str]) -> str:
    if not z:
        return f"E[{y} | {x}=t]"
    zs = ", ".join(z)
    return f"Σ_{{{zs}}} E[{y} | {x}=t, {zs}] P({zs})"
