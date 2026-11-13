"""Run a shadow round, then score it against the bars it pre-registered.

A round (shadow/README.md) is 10-20 studies on real questions, proposed by devbrain
and agentcenter, judged against bars committed before its first run. This module
does the two things a round needs from the platform, in two steps, because the
verdicts must be sealed before any follow-up data exists:

``seal``
    For each study: check the graph, identify the effect from the graph alone, then
    analyse the decision-window data and decide -- the same kernel calls
    ``causal_check_graph``, ``causal_identify`` and ``causal_decide`` make. Every
    verdict is sealed into a hash chain with the round's shadow tag
    (:mod:`causal_decision.shadow`: never emitted, not acted on), and a result's
    follow-up is planned with its due date. Written once, to
    ``results/<date>-v<version>-run<N>.json``; a round is sealed once per
    pre-registration version.

``score``
    Re-check the seal (the chain recomputes, the pre-registration and every study are
    byte-for-byte what was sealed), take each follow-up that is due and whose data
    exists -- the sealed plan's own analysis re-run on the later window, compared by
    :func:`causal_decision.outcome.compare` -- and judge every bar. A follow-up taken
    by an earlier scoring stands; it is never re-measured. Each scoring is written to
    ``scorecards/<date>-v<version>-run<N>.json``, never over an earlier one, and a
    failed round is kept like a passed one.

A bar is ``pass``, ``fail``, ``pending`` (the data that would settle it does not
exist yet: a follow-up not due, a human decision not recorded, a review owed) or
``not_applicable`` (only ``min_narratives_verified``, when no agent wrote prose for
the round). The round is ``failed`` if any bar fails, ``pending`` if any is pending,
and ``passed`` only when every bar has been measured and holds.

Layout of a round directory (``shadow/rounds/<N>``)::

    PRE-REGISTRATION.md   the bars, as one ```toml block (shadow/PRE-REGISTRATION.template.md)
    studies/*.json        one study each (shadow/study-template.json)
    <data files>          each study's shadow.data.decision_window and follow_up_window,
                          as paths relative to the round directory (CSV, Parquet, JSON, JSONL)
    reviews/<id>.md       the written review of a disagreement with the human decision
    results/              seals, written by `seal`
    scorecards/           scorings, written by `score`

    python -m causal_decision.shadow_round seal  shadow/rounds/1
    python -m causal_decision.shadow_round score shadow/rounds/1

What a scorecard does not establish: a round is 10-20 decisions, so every rate it
reports carries a Clopper-Pearson interval and none is stated below ten behind it
(:func:`causal_decision.outcome.rate_estimate`). A passed round says these verdicts
met these bars; it is not a calibration figure for the platform.
"""

from __future__ import annotations

import hashlib
import json
import re
import tomllib
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal

import pandas as pd
from causal_core import analyze
from causal_core.canonical import digest
from causal_core.claims import verify as verify_narrative
from causal_core.contract import AbstentionReason, CausalQuery, CausalResult
from causal_core.graph import Graph, GraphSyntaxError, parse_graph
from causal_core.identification import IdentificationError, InvalidAdjustmentSet, identify
from pydantic import BaseModel, ConfigDict, Field, model_validator

from causal_decision._version import __version__ as decision_version
from causal_decision.contract import DecisionRequest, Verdict
from causal_decision.ledger import SHADOW_TAG, DecisionRecord, append, verify_chain
from causal_decision.outcome import (
    ACTED,
    CalibrationReport,
    FollowUp,
    RateEstimate,
    Realization,
    compare,
    plan,
    rate_estimate,
    report,
)
from causal_decision.policy import decide
from causal_decision.render import explain

#: What a study expects when it is not a planted refusal.
A_VERDICT = "a verdict"

#: The bars a round's pre-registration must set, every one: a bar the scorer could
#: not evaluate, or one quietly left out, would be judged by nobody.
BARS = (
    "max_confident_wrong",
    "min_planted_refused",
    "min_planted_reason_match",
    "disagreements_reviewed",
    "min_narratives_verified",
)
#: The figures `[reported_not_gated]` may name.
METRICS = ("act_right_rate", "experiment_share", "refusal_share_by_reason", "agreement_with_human")

#: The study fields a human may fill in after sealing; everything else is frozen.
HUMAN_FIELDS = ("human_decision", "human_acted")

_RUN = re.compile(r"^(\d{4}-\d{2}-\d{2})-v(\d+)-run(\d+)\.json$")


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class _Loose(BaseModel):
    """Study files are Oracle hypothesis specs: keys this module does not read are kept."""

    model_config = ConfigDict(extra="allow", frozen=True)


class RoundError(ValueError):
    """The round cannot be sealed or scored as it stands; nothing was written."""


# ── the inputs: a pre-registration and its studies ──────────────────────────


class StudyData(_Loose):
    decision_window: str = Field(description="Relative to the round directory.")
    follow_up_window: str = Field(description="Relative to the round directory.")


class ShadowSpec(_Loose):
    kind: Literal["causalplatform"]
    proposed_by: str
    data: StudyData
    follow_up_after: str = Field(pattern=r"^[1-9]\d*d$", description="Days, e.g. '90d'.")
    expect: str = Field(description=f"'{A_VERDICT}', or the refusal reason a planted case expects.")
    human_decision: str | None = None
    human_acted: bool | None = Field(
        default=None,
        description="Whether the people responsible acted, decided blind; None until known. "
        "It is what the platform's verdict is compared with.",
    )
    narrative: str | None = Field(
        default=None,
        description="Prose an agent wrote about this study's analysis, when an agent drove "
        "the flow; checked against the outcome as causal_verify_narrative checks it.",
    )
    tag: str = Field(pattern=SHADOW_TAG, max_length=100)

    @model_validator(mode="after")
    def _expect_is_known(self) -> ShadowSpec:
        reasons = {r.value for r in AbstentionReason}
        if self.expect != A_VERDICT and self.expect not in reasons:
            raise ValueError(
                f"shadow.expect is {self.expect!r}: write '{A_VERDICT}' or one of "
                f"{sorted(reasons)}"
            )
        return self


class Study(_Loose):
    id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    title: str
    graph: str
    design: str
    estimand: str
    query: CausalQuery
    decision: dict[str, Any]
    shadow: ShadowSpec

    @model_validator(mode="after")
    def _consistent(self) -> Study:
        if self.query.design.value != self.design:
            raise ValueError(f"{self.id}: design {self.design!r} but query.design "
                             f"{self.query.design.value!r}")
        if self.query.estimand != self.estimand:
            raise ValueError(f"{self.id}: estimand {self.estimand!r} but query.estimand "
                             f"{self.query.estimand!r}")
        self.request()  # the decision block must be a DecisionRequest
        return self

    @property
    def planted(self) -> bool:
        return self.shadow.expect != A_VERDICT

    def request(self) -> DecisionRequest:
        return DecisionRequest(outcome=self.query.outcome, **self.decision)


class Bars(_Model):
    version: int = Field(ge=1)
    min_studies: int = Field(ge=1)
    min_planted_share: float = Field(ge=0, le=1)
    bars: dict[str, float]
    reported: tuple[str, ...] = ()


def load_prereg(round_dir: Path) -> tuple[Bars, str]:
    """The round's bars, and the sha256 of the file they came from."""
    path = round_dir / "PRE-REGISTRATION.md"
    if not path.is_file():
        raise RoundError(f"{path} does not exist: a round is judged against bars committed "
                         "before it runs")
    raw = path.read_bytes()
    blocks = re.findall(r"```toml\n(.*?)```", raw.decode("utf-8"), re.S)
    if len(blocks) != 1:
        raise RoundError(f"{path} must hold exactly one ```toml block, not {len(blocks)}")
    cfg = tomllib.loads(blocks[0])
    bars = cfg.get("bars") or {}
    missing, unknown = set(BARS) - set(bars), set(bars) - set(BARS)
    if missing or unknown:
        raise RoundError(f"{path}: [bars] must set exactly {list(BARS)}; missing "
                         f"{sorted(missing)}, unknown {sorted(unknown)}")
    reported = tuple((cfg.get("reported_not_gated") or {}).get("metrics") or ())
    if extra := sorted(set(reported) - set(METRICS)):
        raise RoundError(f"{path}: [reported_not_gated] names {extra}, which this scorer "
                         f"does not compute ({list(METRICS)})")
    r = cfg.get("round") or {}
    try:
        out = Bars(
            version=r["version"],
            min_studies=r["min_studies"],
            min_planted_share=r["min_planted_share"],
            bars={k: float(v) for k, v in bars.items()},
            reported=reported,
        )
    except (KeyError, ValueError) as exc:
        raise RoundError(f"{path}: [round] needs version, min_studies and min_planted_share "
                         f"({exc})") from exc
    return out, "sha256:" + hashlib.sha256(raw).hexdigest()


def load_studies(round_dir: Path) -> list[tuple[Study, str]]:
    """Every study of the round with its frozen digest, in id order."""
    files = sorted((round_dir / "studies").glob("*.json"))
    if not files:
        raise RoundError(f"{round_dir / 'studies'} holds no study")
    out: list[tuple[Study, str]] = []
    for f in files:
        try:
            raw = json.loads(f.read_text())
            study = Study.model_validate(raw)
        except ValueError as exc:
            raise RoundError(f"{f}: {exc}") from exc
        out.append((study, frozen_digest(raw)))
    ids = Counter(s.id for s, _ in out)
    if dup := sorted(i for i, n in ids.items() if n > 1):
        raise RoundError(f"study ids repeat: {dup}")
    tags = {s.shadow.tag for s, _ in out}
    if len(tags) != 1:
        raise RoundError(f"a round has one shadow tag; its studies carry {sorted(tags)}")
    return sorted(out, key=lambda x: x[0].id)


def frozen_digest(raw: dict[str, Any]) -> str:
    """A study's digest without the fields a human fills in afterwards.

    Everything else -- the question, the graph, the decision thresholds, the
    declared segments, the expected refusal -- is frozen at submission; a change
    after the seal would be the researcher degree of freedom the round exists to
    remove.
    """
    shadow = {k: v for k, v in (raw.get("shadow") or {}).items() if k not in HUMAN_FIELDS}
    return digest({**raw, "shadow": shadow})


def _inside(round_dir: Path, rel: str) -> Path:
    path = (round_dir / rel).resolve()
    if not path.is_relative_to(round_dir.resolve()):
        raise RoundError(f"{rel} is outside the round directory")
    return path


def read_table(path: Path) -> pd.DataFrame:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return pd.read_csv(path)
    if suffix == ".parquet":
        return pd.read_parquet(path)
    if suffix in (".json", ".jsonl"):
        return pd.read_json(path, lines=suffix == ".jsonl")
    raise RoundError(f"{path.name}: read CSV, Parquet, JSON or JSONL, not {suffix or 'no suffix'}")


# ── seal ─────────────────────────────────────────────────────────────────────


class GraphCheck(_Model):
    observed: tuple[str, ...]
    latent: tuple[str, ...]
    unreviewed_llm_edges: tuple[str, ...]


class Identification(_Model):
    identified: bool
    detail: str = ""


class SealedStudy(_Model):
    id: str
    study_digest: str
    planted: bool
    expect: str
    graph_check: GraphCheck
    identification: Identification
    outcome_kind: Literal["result", "refusal", "apparatus_failure"]
    refusal_reason: str | None = None
    verdict: Verdict
    explanation: str
    entry_digest: str
    followup: FollowUp | None = Field(
        default=None, description="None for a refusal: it predicted nothing to measure."
    )
    follow_up_due: str
    narrative_verified: bool | None = None
    narrative_problems: tuple[str, ...] = ()


class SealedRound(_Model):
    suite: Literal["shadow-round"] = "shadow-round"
    round: str
    tag: str
    prereg_sha256: str
    prereg_version: int
    git: str
    sealed_at: str
    causal_core_version: str
    causal_decision_version: str = decision_version
    studies: tuple[SealedStudy, ...]
    records: tuple[DecisionRecord, ...]
    chain_head: str


def _check_and_identify(study: Study) -> tuple[GraphCheck, Identification]:
    try:
        g = Graph.from_spec(parse_graph(study.graph))
    except (GraphSyntaxError, ValueError) as exc:
        raise RoundError(f"{study.id}: the graph does not parse: {exc}") from exc
    check = GraphCheck(
        observed=tuple(sorted(g.observed)),
        latent=tuple(sorted(n for n in g.latent if not n.startswith("⟨"))),
        unreviewed_llm_edges=tuple(g.unreviewed_llm_edges()),
    )
    try:
        cert = identify(g, study.query)
    except InvalidAdjustmentSet as exc:
        return check, Identification(identified=False, detail=exc.detail)
    except (IdentificationError, ValueError) as exc:
        return check, Identification(identified=False, detail=str(exc))
    ok = cert.status.value == "identified"
    return check, Identification(
        identified=ok, detail="" if ok else f"not identified: {cert.general_id}"
    )


def _next_run(directory: Path, day: str, version: int) -> int:
    runs = [
        int(m.group(3))
        for p in directory.glob(f"{day}-v{version}-run*.json")
        if (m := _RUN.match(p.name))
    ]
    return max(runs, default=0) + 1


def _write_once(path: Path, model: BaseModel) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x") as fh:  # never over an earlier run
        fh.write(model.model_dump_json(indent=2) + "\n")


def seal(round_dir: Path, *, now: datetime, git: str) -> tuple[Path, SealedRound]:
    """Analyse, decide and seal every study of the round, as shadow decisions.

    Refuses, writing nothing, when the round is not ready: no pre-registration, a
    study that does not validate, decision-window data missing, follow-up data
    already present (a verdict sealed after its follow-up exists is not a
    prediction), or a seal already on file for this pre-registration version.
    """
    from causal_core import __version__ as core_version

    if now.tzinfo is None:
        raise RoundError("`now` must carry a UTC offset")
    round_dir = round_dir.resolve()
    bars, prereg_sha = load_prereg(round_dir)
    studies = load_studies(round_dir)
    tag = studies[0][0].shadow.tag
    results = round_dir / "results"
    if sealed := sorted(results.glob(f"*-v{bars.version}-run*.json")):
        raise RoundError(f"this round is already sealed under v{bars.version} "
                         f"({sealed[0].name}); a round is sealed once per version")
    frames: dict[str, pd.DataFrame] = {}
    for study, _ in studies:
        before = _inside(round_dir, study.shadow.data.decision_window)
        after = _inside(round_dir, study.shadow.data.follow_up_window)
        if not before.is_file():
            raise RoundError(f"{study.id}: no decision-window data at {before}")
        if after.exists():
            raise RoundError(f"{study.id}: follow-up data already exists at {after}; verdicts "
                             "are sealed before any follow-up data exists")
        frames[study.id] = read_table(before)

    recorded_at = now.isoformat()
    tail: DecisionRecord | None = None
    sealed_studies: list[SealedStudy] = []
    records: list[DecisionRecord] = []
    for study, frozen in studies:
        check, ident = _check_and_identify(study)
        outcome = analyze(frames[study.id], study.graph, query=study.query)
        decision = decide(outcome, study.request())
        explanation = explain(decision, outcome)
        due = (now + timedelta(days=int(study.shadow.follow_up_after[:-1]))).isoformat()
        tail = append(tail, decision, outcome, recorded_at=recorded_at, review_by=due,
                      shadow=tag)
        records.append(tail)
        followup = (
            plan(decision, outcome, graph=study.graph, observe_from=recorded_at)
            if isinstance(outcome, CausalResult)
            else None
        )
        narrative_ok: bool | None = None
        problems: tuple[str, ...] = ()
        if study.shadow.narrative is not None:
            v = verify_narrative(study.shadow.narrative, outcome)
            narrative_ok, problems = v.ok, tuple(v.reasons())
        sealed_studies.append(SealedStudy(
            id=study.id,
            study_digest=frozen,
            planted=study.planted,
            expect=study.shadow.expect,
            graph_check=check,
            identification=ident,
            outcome_kind=outcome.kind,
            refusal_reason=decision.evidence.refusal_reason,
            verdict=decision.verdict,
            explanation=explanation,
            entry_digest=tail.entry_digest,
            followup=followup,
            follow_up_due=due,
            narrative_verified=narrative_ok,
            narrative_problems=problems,
        ))
    if tail is None:  # load_studies refuses an empty round; a raise survives python -O
        raise RoundError("the round has no study to seal")
    out = SealedRound(
        round=round_dir.name,
        tag=tag,
        prereg_sha256=prereg_sha,
        prereg_version=bars.version,
        git=git,
        sealed_at=recorded_at,
        causal_core_version=core_version,
        studies=tuple(sealed_studies),
        records=tuple(records),
        chain_head=tail.entry_digest,
    )
    day = now.date().isoformat()
    path = results / f"{day}-v{bars.version}-run{_next_run(results, day, bars.version)}.json"
    _write_once(path, out)
    return path, out


# ── score ────────────────────────────────────────────────────────────────────

BarStatus = Literal["pass", "fail", "pending", "not_applicable"]


class BarResult(_Model):
    name: str
    bar: float
    value: float | None = None
    numerator: int | None = None
    denominator: int | None = None
    status: BarStatus
    detail: str


class StudyScore(_Model):
    id: str
    planted: bool
    expect: str
    verdict: Verdict
    outcome_kind: str
    refusal_reason: str | None = None
    reason_matched: bool | None = Field(
        default=None, description="Planted studies: whether the refusal named the expected reason."
    )
    followup: Literal["not_planned", "not_due", "no_data", "measured"]
    measured_in: str | None = Field(
        default=None, description="The scorecard the follow-up was first taken in."
    )
    realization: Realization | None = None
    confident_wrong: bool | None = Field(
        default=None,
        description="An act or act_with_approval whose follow-up interval lies wholly on the "
        "harmful side of zero.",
    )
    human_acted: bool | None = None
    agrees_with_human: bool | None = None
    reviewed: bool | None = Field(
        default=None, description="For a disagreement: whether reviews/<id>.md exists."
    )


class Scorecard(_Model):
    suite: Literal["shadow-round-scorecard"] = "shadow-round-scorecard"
    round: str
    tag: str
    seal_file: str
    seal_sha256: str
    prereg_sha256: str
    prereg_version: int
    git: str
    scored_at: str
    status: Literal["passed", "failed", "pending"]
    bars: tuple[BarResult, ...]
    studies: tuple[StudyScore, ...]
    reported: dict[str, Any] = Field(
        default_factory=dict, description="[reported_not_gated]: figures, not judged."
    )
    calibration: CalibrationReport = Field(
        description="causal_decision.outcome.report over this round's follow-ups -- the same "
        "report the platform keeps for real decisions, never mixed with it."
    )
    note: str = (
        "Bars are judged as pre-registered. pending means the data that would settle a bar does "
        "not exist yet; it is not a pass. Every rate is a Clopper-Pearson interval and none is "
        "stated below ten behind it: a round is too small to be a calibration figure."
    )


def _seal_of(round_dir: Path, version: int) -> Path:
    seals = sorted((round_dir / "results").glob(f"*-v{version}-run*.json"))
    if not seals:
        raise RoundError(f"no seal under v{version} in {round_dir / 'results'}; run `seal` first")
    if len(seals) > 1:
        raise RoundError(f"more than one seal under v{version}: {[p.name for p in seals]}")
    return seals[0]


def _earlier_measurements(round_dir: Path, seal_sha: str) -> dict[str, tuple[str, Realization]]:
    """The first follow-up taken for each study by earlier scorings of this seal."""
    out: dict[str, tuple[str, Realization]] = {}
    cards = []
    for p in (round_dir / "scorecards").glob("*.json"):
        if m := _RUN.match(p.name):
            cards.append(((m.group(1), int(m.group(2)), int(m.group(3))), p))
    for _, p in sorted(cards):
        card = Scorecard.model_validate_json(p.read_text())
        if card.seal_sha256 != seal_sha:
            continue
        for s in card.studies:
            if s.realization is not None and s.id not in out:
                out[s.id] = (s.measured_in or p.name, s.realization)
    return out


def _share_bar(name: str, bar: float, hits: int, n: int, what: str) -> BarResult:
    if n == 0:
        return BarResult(name=name, bar=bar, numerator=0, denominator=0, status="fail",
                         detail=f"no {what} in the round: the bar inspected nothing")
    value = hits / n
    return BarResult(name=name, bar=bar, value=value, numerator=hits, denominator=n,
                     status="pass" if value >= bar else "fail",
                     detail=f"{hits} of {n} {what}")


def score(round_dir: Path, *, now: datetime, git: str) -> tuple[Path, Scorecard]:
    """Take the follow-ups that are due, judge every bar, and write a new scorecard."""
    if now.tzinfo is None:
        raise RoundError("`now` must carry a UTC offset")
    round_dir = round_dir.resolve()
    bars, prereg_sha = load_prereg(round_dir)
    seal_path = _seal_of(round_dir, bars.version)
    seal_bytes = seal_path.read_bytes()
    sealed = SealedRound.model_validate_json(seal_bytes)
    seal_sha = "sha256:" + hashlib.sha256(seal_bytes).hexdigest()
    if sealed.prereg_sha256 != prereg_sha:
        raise RoundError("PRE-REGISTRATION.md has changed since the round was sealed; the bars "
                         "move only with a version bump and a changelog entry")
    chain = verify_chain(list(sealed.records))
    if not chain.ok or chain.head_digest != sealed.chain_head:
        raise RoundError(f"the sealed chain does not verify: {chain.reason or 'head moved'}")
    if any(r.shadow != sealed.tag for r in sealed.records):
        raise RoundError(f"a sealed record is not tagged {sealed.tag!r}")
    by_digest = {r.entry_digest: r for r in sealed.records}
    studies = {s.id: (s, d) for s, d in load_studies(round_dir)}
    if set(studies) != {s.id for s in sealed.studies}:
        raise RoundError("the round's studies are not the ones sealed: "
                         f"{sorted(set(studies) ^ {s.id for s in sealed.studies})}")

    earlier = _earlier_measurements(round_dir, seal_sha)
    day = now.date().isoformat()
    cards = round_dir / "scorecards"
    name = f"{day}-v{bars.version}-run{_next_run(cards, day, bars.version)}.json"

    scores: list[StudyScore] = []
    for s in sealed.studies:
        study, frozen = studies[s.id]
        if frozen != s.study_digest:
            raise RoundError(f"{s.id} has changed since it was sealed; only "
                             f"{list(HUMAN_FIELDS)} may be filled in afterwards")
        record = by_digest.get(s.entry_digest)
        if record is None or record.decision.verdict != s.verdict:
            raise RoundError(f"{s.id}: its sealed entry is not in the chain")
        status: Literal["not_planned", "not_due", "no_data", "measured"]
        realization: Realization | None = None
        measured_in: str | None = None
        if s.followup is None:
            status = "not_planned"
        elif s.id in earlier:
            status = "measured"
            measured_in, realization = earlier[s.id]
        elif now < datetime.fromisoformat(s.follow_up_due):
            status = "not_due"
        else:
            later_path = _inside(round_dir, study.shadow.data.follow_up_window)
            if not later_path.is_file():
                status = "no_data"
            else:
                later = analyze(read_table(later_path), s.followup.graph,
                                query=s.followup.query)
                realization = compare(s.followup, later)
                status, measured_in = "measured", name
        wrong: bool | None = None
        if s.verdict in ACTED and realization is not None:
            ci = realization.realized_ci
            wrong = ci is not None and ci[1] < 0
        acted = study.shadow.human_acted
        agrees = None if acted is None or s.planted else (s.verdict in ACTED) == acted
        reviewed = None
        if agrees is False:
            review = round_dir / "reviews" / f"{s.id}.md"
            reviewed = review.is_file() and bool(review.read_text().strip())
        scores.append(StudyScore(
            id=s.id,
            planted=s.planted,
            expect=s.expect,
            verdict=s.verdict,
            outcome_kind=s.outcome_kind,
            refusal_reason=s.refusal_reason,
            reason_matched=(s.refusal_reason == s.expect) if s.planted else None,
            followup=status,
            measured_in=measured_in,
            realization=realization,
            confident_wrong=wrong,
            human_acted=acted,
            agrees_with_human=agrees,
            reviewed=reviewed,
        ))

    results = _judge(bars, sealed, scores)
    worst = {r.status for r in results}
    overall: Literal["passed", "failed", "pending"] = (
        "failed" if "fail" in worst else "pending" if "pending" in worst else "passed"
    )
    realizations = [s.realization for s in scores if s.realization is not None]
    card = Scorecard(
        round=sealed.round,
        tag=sealed.tag,
        seal_file=seal_path.name,
        seal_sha256=seal_sha,
        prereg_sha256=prereg_sha,
        prereg_version=bars.version,
        git=git,
        scored_at=now.isoformat(),
        status=overall,
        bars=tuple(results),
        studies=tuple(scores),
        reported=_reported(bars, scores),
        calibration=report(realizations),
    )
    path = cards / name
    _write_once(path, card)
    return path, card


def _judge(bars: Bars, sealed: SealedRound, scores: list[StudyScore]) -> list[BarResult]:
    n = len(scores)
    planted = [s for s in scores if s.planted]
    out = [
        BarResult(
            name="min_studies", bar=bars.min_studies, value=n, numerator=n,
            status="pass" if n >= bars.min_studies else "fail",
            detail=f"{n} studies sealed",
        ),
        BarResult(
            name="min_planted_share", bar=bars.min_planted_share, value=len(planted) / n,
            numerator=len(planted), denominator=n,
            status="pass" if len(planted) / n >= bars.min_planted_share else "fail",
            detail=f"{len(planted)} of {n} studies are planted refusals",
        ),
    ]

    acted = [s for s in scores if s.verdict in ACTED]
    wrong = sum(1 for s in acted if s.confident_wrong)
    waiting = sum(1 for s in acted if s.followup in ("not_due", "no_data"))
    unresolved = sum(1 for s in acted if s.realization is not None and not s.realization.decidable)
    cap = bars.bars["max_confident_wrong"]
    out.append(BarResult(
        name="max_confident_wrong", bar=cap, value=wrong, numerator=wrong,
        denominator=len(acted),
        status="fail" if wrong > cap else "pending" if waiting else "pass",
        detail=f"{wrong} of {len(acted)} acted verdicts confidently wrong; {waiting} follow-up(s) "
        f"not yet taken; {unresolved} undecidable (the later data could not say, counted, not "
        "judged)",
    ))

    out.append(_share_bar("min_planted_refused", bars.bars["min_planted_refused"],
                          sum(1 for s in planted if s.outcome_kind == "refusal"), len(planted),
                          "planted studies refused"))
    out.append(_share_bar("min_planted_reason_match", bars.bars["min_planted_reason_match"],
                          sum(1 for s in planted if s.reason_matched), len(planted),
                          "planted studies refused with the expected reason"))

    real = [s for s in scores if not s.planted]
    unknown = sum(1 for s in real if s.agrees_with_human is None)
    disagree = [s for s in real if s.agrees_with_human is False]
    reviewed = sum(1 for s in disagree if s.reviewed)
    share = reviewed / len(disagree) if disagree else 1.0
    out.append(BarResult(
        name="disagreements_reviewed", bar=bars.bars["disagreements_reviewed"], value=share,
        numerator=reviewed, denominator=len(disagree),
        status="pending" if unknown or share < bars.bars["disagreements_reviewed"] else "pass",
        detail=f"{len(disagree)} disagreement(s) with the human decision, {reviewed} reviewed; "
        f"{unknown} study(ies) without a recorded human decision. A missing review is owed, "
        "not a wrong answer",
    ))

    told = [s for s in sealed.studies if s.narrative_verified is not None]
    if told:
        out.append(_share_bar("min_narratives_verified", bars.bars["min_narratives_verified"],
                              sum(1 for s in told if s.narrative_verified), len(told),
                              "agent narratives verified"))
    else:
        out.append(BarResult(
            name="min_narratives_verified", bar=bars.bars["min_narratives_verified"],
            numerator=0, denominator=0, status="not_applicable",
            detail="no agent drove this round's flow, so there is no agent prose to verify",
        ))
    return out


def _reported(bars: Bars, scores: list[StudyScore]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    decided = [s for s in scores if s.realization is not None and s.realization.decidable]
    acted = [s for s in decided if s.verdict in ACTED and s.realization is not None
             and s.realization.cleared_worthwhile is not None]
    known = [s for s in scores if s.agrees_with_human is not None]
    figures: dict[str, Any] = {
        "act_right_rate": lambda: _rate(
            sum(1 for s in acted if s.realization and s.realization.cleared_worthwhile),
            len(acted),
        ),
        "experiment_share": lambda: _rate(
            sum(1 for s in scores if s.verdict is Verdict.experiment), len(scores)
        ),
        "refusal_share_by_reason": lambda: {
            "denominator": len(scores),
            "counts": dict(Counter(s.refusal_reason for s in scores if s.refusal_reason)),
        },
        "agreement_with_human": lambda: _rate(
            sum(1 for s in known if s.agrees_with_human), len(known)
        ),
    }
    for name in bars.reported:
        out[name] = figures[name]()
    return out


def _rate(hits: int, n: int) -> dict[str, Any]:
    est: RateEstimate = rate_estimate(hits, n)
    return est.model_dump(mode="json")


# ── command line ─────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    """`seal` exits 0 once written; `score` exits 0 passed, 1 failed, 2 pending."""
    import argparse
    import sys
    from datetime import UTC

    from causal_core.bench.t0 import git_sha

    ap = argparse.ArgumentParser(description="Seal or score a shadow round (shadow/README.md)")
    ap.add_argument("step", choices=("seal", "score"))
    ap.add_argument("round_dir", type=Path)
    args = ap.parse_args(argv)
    now = datetime.now(UTC)
    sha = git_sha(Path(__file__))
    try:
        if args.step == "seal":
            path, sealed = seal(args.round_dir, now=now, git=sha)
            print(json.dumps({"sealed": str(path), "studies": len(sealed.studies),
                              "verdicts": dict(Counter(str(s.verdict) for s in sealed.studies))}))
            return 0
        path, card = score(args.round_dir, now=now, git=sha)
    except RoundError as exc:
        print(f"shadow round: {exc}", file=sys.stderr)
        return 3
    print(json.dumps({"scorecard": str(path), "status": card.status,
                      "bars": {b.name: b.status for b in card.bars}}))
    return {"passed": 0, "failed": 1, "pending": 2}[card.status]


if __name__ == "__main__":
    raise SystemExit(main())
