"""
agentcenter / the Oracle: the owner's second brain as a source of suggestions, and
a place to report decisions back to.

agentcenter (default http://127.0.0.1:8765) is the local hub; its Oracle (default
http://127.0.0.1:8767, loopback only) keeps typed causal hypotheses -- arms, graph,
design, estimand, what acting is worth -- plus a ranked queue of next steps. This
module reads them and turns them into suggestions the analysis page can open:

- an Oracle hypothesis becomes an analysis prefilled with its graph, question and
  decision thresholds; when its data binding is one this platform can fetch (a
  pair of agentcenter benchmark runs), the data comes too;
- a review-queue item is a next step, with how to do it;
- a learning-radar improvement target is a suggestion for a project.

Reads are GETs with short timeouts; each source fails on its own and is reported
as unreachable, never raised. The one write is optional (`AGENTCENTER_EMIT`): a
recorded decision is sent to agentcenter's `POST /events/emit`, which lands in the
Central Brain's event log and outbox -- the channel agentcenter documents for
results. Pre-registrations and verdicts in the Oracle stay human-only; nothing here
writes them.

Configuration (environment):
    AGENTCENTER_URL   agentcenter base URL; unset disables the connector
    ORACLE_URL        the Oracle's base URL (default: http://127.0.0.1:8767 when
                      AGENTCENTER_URL is set)
    AGENTCENTER_EMIT  "true" to report recorded decisions as events
    AGENTCENTER_PROJECT  project id for emitted events (default "causalplatform")
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any, Literal

import httpx
import structlog
from pydantic import BaseModel, Field

log = structlog.get_logger(__name__)

TIMEOUT = httpx.Timeout(4.0, connect=1.0)
#: Tests replace this with an httpx.MockTransport; None is the network.
_transport: httpx.AsyncBaseTransport | None = None
Cell = float | int | str | bool | None


@dataclass(frozen=True)
class Config:
    agentcenter_url: str | None
    oracle_url: str | None
    emit: bool
    project: str

    @property
    def enabled(self) -> bool:
        return self.agentcenter_url is not None


def config() -> Config:
    """Read the environment each call, so a changed .env needs no restart of this module."""
    ac = (os.environ.get("AGENTCENTER_URL") or "").rstrip("/") or None
    oracle = (os.environ.get("ORACLE_URL") or "").rstrip("/") or (
        "http://127.0.0.1:8767" if ac else None
    )
    return Config(
        agentcenter_url=ac,
        oracle_url=oracle,
        emit=os.environ.get("AGENTCENTER_EMIT", "").lower() in ("1", "true", "yes"),
        project=os.environ.get("AGENTCENTER_PROJECT") or "causalplatform",
    )


# ── Suggestions ──────────────────────────────────────────────────────────────


class StudyPrefill(BaseModel):
    """What the analysis page fills in from a hypothesis."""

    graph: str
    treatment: str
    outcome: str
    design: Literal["observational", "randomized"] = "observational"
    estimand: Literal["ate", "att"] = "ate"
    action: str | None = None
    direction: Literal["increase", "decrease"] | None = None
    minimum_worthwhile_effect: float | None = None
    arms: dict[str, str] = Field(default_factory=dict)
    outcome_label: str | None = None


class DataBinding(BaseModel):
    """Where the hypothesis's data lives, and whether this platform can fetch it."""

    fetchable: bool
    builder: str
    note: str


class Suggestion(BaseModel):
    id: str
    source: Literal["oracle.hypothesis", "oracle.review", "radar"]
    kind: Literal["experiment", "next_step", "improvement"]
    title: str
    why: str = ""
    project: str | None = None
    detail: list[str] = Field(default_factory=list)
    zone: str | None = Field(default=None, description="The Oracle's zone, e.g. 'probe first'.")
    rung: str | None = Field(default=None, description="How far the evidence has got.")
    study: StudyPrefill | None = None
    data: DataBinding | None = None
    time: str | None = None


class SourceStatus(BaseModel):
    name: str
    url: str | None
    reachable: bool
    error: str | None = None
    count: int = 0


class Suggestions(BaseModel):
    enabled: bool
    project: str
    emit: bool
    sources: list[SourceStatus]
    suggestions: list[Suggestion]


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=TIMEOUT, transport=_transport)


async def _get(client: httpx.AsyncClient, url: str) -> Any:
    resp = await client.get(url)
    resp.raise_for_status()
    return resp.json()


# The graph's roles in an Oracle spec: its arm variable is always `arm`, its outcome
# `outcome` (oracle/hypotheses.py builds the query that way).
_ARM, _OUTCOME = "arm", "outcome"
#: Bindings this platform can fetch itself, through agentcenter's API.
_FETCHABLE = {"bench_pair"}


def _hypothesis(h: dict[str, Any], wisdom: dict[str, dict[str, Any]]) -> Suggestion:
    spec = h.get("spec") or {}
    w = wisdom.get(h.get("hyp_id", ""), {})
    decision = spec.get("decision") or {}
    binding = spec.get("binding") or {}
    builder = str(binding.get("builder") or "none")
    if builder in _FETCHABLE:
        data = DataBinding(
            fetchable=True,
            builder=builder,
            note="agentcenter benchmark items for the two runs, one row per item and arm",
        )
    elif builder == "none":
        data = DataBinding(
            fetchable=False,
            builder=builder,
            note=str(binding.get("missing") or "no data has been collected for this yet"),
        )
    else:
        data = DataBinding(
            fetchable=False,
            builder=builder,
            note=f"built by the Oracle's `{builder}` binding from its own store; export it "
            f"as a CSV with columns `{_ARM}` (1 = {(spec.get('arms') or {}).get('1', 'treated')}) "
            f"and `{_OUTCOME}` to analyse it here",
        )
    design = spec.get("design")
    direction = decision.get("direction")
    detail = [str(c) for c in spec.get("caveats") or []]
    for p in (h.get("preflight") or {}).get("predicted") or []:
        detail.append(f"preflight: {p}")
    return Suggestion(
        id=f"oracle:{h['hyp_id']}",
        source="oracle.hypothesis",
        kind="experiment",
        title=str(spec.get("title") or h["hyp_id"]),
        why=str(w.get("why") or spec.get("statement") or ""),
        project=w.get("project"),
        detail=detail,
        zone=w.get("zone"),
        rung=w.get("rung_name"),
        study=StudyPrefill(
            graph=str(spec.get("graph") or f"{_ARM} -> {_OUTCOME}"),
            treatment=_ARM,
            outcome=_OUTCOME,
            design=design if design in ("observational", "randomized") else "observational",
            estimand="att" if spec.get("estimand") == "att" else "ate",
            action=decision.get("action"),
            direction=direction if direction in ("increase", "decrease") else None,
            minimum_worthwhile_effect=decision.get("minimum_worthwhile_effect"),
            arms={str(k): str(v) for k, v in (spec.get("arms") or {}).items()},
            outcome_label=spec.get("outcome_label"),
        ),
        data=data,
    )


def _review(item: dict[str, Any]) -> Suggestion:
    return Suggestion(
        id=f"review:{item.get('kind')}:{item.get('id')}"[:300],
        source="oracle.review",
        kind="next_step",
        title=str(item.get("what") or item.get("about") or "next step"),
        why=str(item.get("unlocks") or item.get("why_you") or ""),
        project=item.get("project"),
        detail=[str(s) for s in item.get("how") or []],
        time=item.get("time"),
    )


_TARGETS = re.compile(r"^## Improvement targets\s*$(.*?)(?=^## |\Z)", re.M | re.S)
_PROJECT = re.compile(r"^\*\*(.+?)\*\*\s*$", re.M)
_ITEM = re.compile(r"^\s*\d+\.\s+\*\*(.+?)\*\*\s*(.*)$", re.M)


def radar_targets(markdown: str, date: str) -> list[Suggestion]:
    """The "Improvement targets" section of a learning-radar report, per project."""
    m = _TARGETS.search(markdown)
    if not m:
        return []
    out: list[Suggestion] = []
    heads = list(_PROJECT.finditer(m.group(1)))
    for i, head in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(m.group(1))
        block = m.group(1)[head.end() : end]
        for n, item in enumerate(_ITEM.finditer(block), start=1):
            out.append(
                Suggestion(
                    id=f"radar:{date}:{head.group(1)}:{n}",
                    source="radar",
                    kind="improvement",
                    title=item.group(1).rstrip("."),
                    why=item.group(2).strip(),
                    project=head.group(1),
                    detail=[f"learning radar {date}"],
                )
            )
    return out


async def suggestions(cfg: Config | None = None) -> Suggestions:
    cfg = cfg or config()
    if not cfg.enabled:
        return Suggestions(
            enabled=False, project=cfg.project, emit=cfg.emit, sources=[], suggestions=[]
        )
    sources: list[SourceStatus] = []
    found: list[Suggestion] = []
    async with _client() as client:
        # Oracle: hypotheses (with wisdom's zone and why), then the review queue.
        status = SourceStatus(name="oracle", url=cfg.oracle_url, reachable=False)
        try:
            hyps = await _get(client, f"{cfg.oracle_url}/api/hypotheses")
            wisdom_raw = await _get(client, f"{cfg.oracle_url}/api/wisdom")
            wisdom = {w["hyp_id"]: w for w in wisdom_raw.get("hypotheses") or [] if "hyp_id" in w}
            got = [_hypothesis(h, wisdom) for h in hyps.get("hypotheses") or [] if "hyp_id" in h]
            review = await _get(client, f"{cfg.oracle_url}/api/review")
            got += [_review(q) for q in review.get("queue") or []]
            found += got
            status.reachable, status.count = True, len(got)
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            status.error = f"{type(exc).__name__}: {exc}"[:300]
        sources.append(status)

        status = SourceStatus(name="agentcenter", url=cfg.agentcenter_url, reachable=False)
        try:
            runs = await _get(client, f"{cfg.agentcenter_url}/insights/radar")
            got = []
            if isinstance(runs, list) and runs:
                date = str(runs[0].get("date"))
                report = await _get(client, f"{cfg.agentcenter_url}/insights/radar/{date}")
                got = radar_targets(str(report.get("content") or ""), date)
            found += got
            status.reachable, status.count = True, len(got)
        except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError) as exc:
            status.error = f"{type(exc).__name__}: {exc}"[:300]
        sources.append(status)
    return Suggestions(
        enabled=True, project=cfg.project, emit=cfg.emit, sources=sources, suggestions=found
    )


# ── Data for a hypothesis ────────────────────────────────────────────────────


class BoundData(BaseModel):
    suggestion_id: str
    columns: dict[str, list[Cell]]
    sources: list[str]
    note: str


class BindingUnavailableError(Exception):
    """The hypothesis's data cannot be fetched from here; the message says why."""


async def bound_data(hyp_id: str, cfg: Config | None = None) -> BoundData:
    """Fetch a hypothesis's data through agentcenter, for the bindings that allow it.

    `bench_pair`: two benchmark runs over the same items; arm 1 is `treated_run`,
    outcome is whether the item passed -- the Oracle's own construction
    (agentcenter oracle/bindings.py), read here through `GET /benchmarks/results/
    {run}/items` instead of its database.
    """
    cfg = cfg or config()
    if not cfg.enabled:
        raise BindingUnavailableError("agentcenter is not configured (AGENTCENTER_URL)")
    async with _client() as client:
        try:
            hyps = await _get(client, f"{cfg.oracle_url}/api/hypotheses")
        except httpx.HTTPError as exc:
            raise BindingUnavailableError(f"the Oracle is unreachable: {exc}") from exc
        spec = next(
            (
                h.get("spec") or {}
                for h in hyps.get("hypotheses") or []
                if h.get("hyp_id") == hyp_id
            ),
            None,
        )
        if spec is None:
            raise LookupError(f"no hypothesis {hyp_id}")
        binding = spec.get("binding") or {}
        builder = binding.get("builder")
        if builder == "none":
            raise BindingUnavailableError(
                f"{hyp_id} has no data yet: {binding.get('missing') or 'none collected'}"
            )
        if builder != "bench_pair":
            raise BindingUnavailableError(
                f"{hyp_id}'s data comes from the Oracle's `{builder}` binding, which this "
                "platform cannot fetch; export it as a CSV"
            )
        params = binding.get("params") or {}
        items: list[Any] = []
        arms: list[Any] = []
        outcome: list[Any] = []
        sources: list[str] = []
        for arm, key in ((1, "treated_run"), (0, "control_run")):
            run = str(params.get(key) or "")
            try:
                rows = await _get(client, f"{cfg.agentcenter_url}/benchmarks/results/{run}/items")
            except httpx.HTTPError as exc:
                raise BindingUnavailableError(f"benchmark run {run}: {exc}") from exc
            if not rows:
                raise BindingUnavailableError(f"benchmark run {run} has no items")
            for r in rows:
                items.append(str(r.get("item_id")))
                arms.append(arm)
                outcome.append(1 if r.get("correct") is True else 0)
            sources.append(f"agentcenter:runs:{run} ({len(rows)} items)")
    return BoundData(
        suggestion_id=f"oracle:{hyp_id}",
        columns={"item": items, _ARM: arms, _OUTCOME: outcome},
        sources=sources,
        note="Both arms answered the same items; analysed as two groups, as the Oracle does.",
    )


# ── Reporting decisions back ─────────────────────────────────────────────────


async def emit_decision(payload: dict[str, Any], cfg: Config | None = None) -> bool:
    """Send a recorded decision to agentcenter's event log. Never raises.

    Returns whether agentcenter accepted it. A failure is logged and otherwise
    ignored: the decision is already sealed in this platform's ledger, which is
    the record of truth; the event is a copy for the second brain.
    """
    cfg = cfg or config()
    if not (cfg.enabled and cfg.emit):
        return False
    body = {
        "kind": "causalplatform.decision.recorded",
        "project": cfg.project,
        "agent": "causalplatform",
        "payload": payload,
    }
    try:
        async with _client() as client:
            resp = await client.post(f"{cfg.agentcenter_url}/events/emit", json=body)
            resp.raise_for_status()
        return True
    except httpx.HTTPError as exc:
        log.warning("agentcenter.emit.failed", error=str(exc)[:300])
        return False
