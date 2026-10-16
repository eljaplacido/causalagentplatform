"""
A tamper-evident record of what was decided, on what evidence, by whom.

A verdict nobody kept is advice. This turns each decision into an entry in a hash
chain: the entry carries the decision, the digests of the evidence behind it, and
the approval that let it happen, plus the digest of the entry before it. Change
anything in a past entry and every digest after it stops matching.

**What this does not do.** A hash chain makes tampering *detectable*, not
impossible. Anyone who can rewrite the store can rewrite the whole chain from the
altered entry onward and it will verify again. Detection only becomes prevention
when a digest is published somewhere the writer does not control -- an append-only
database, a countersignature, another party's copy. That is a deployment decision,
so the chain reports its head digest and leaves anchoring to the caller rather than
implying a guarantee it cannot make on its own.

Everything here is pure. No clock (the caller supplies the timestamp), no store,
no network, so a chain can be rebuilt from its records and re-verified anywhere.
"""

from __future__ import annotations

from collections.abc import Collection
from datetime import datetime
from typing import Literal

from causal_core.canonical import digest
from pydantic import BaseModel, ConfigDict, Field, model_validator

from causal_decision.assumptions import ModelRef
from causal_decision.contract import Decision, Verdict

#: What the first entry points back at. A fixed, recognisable value: an empty
#: string would be indistinguishable from a missing link.
GENESIS = "sha256:" + "0" * 64


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class Approval(_Model):
    """A person taking responsibility for an action the evidence did not automate."""

    approver_id: str
    approved_at: str = Field(description="ISO 8601, from the caller's clock.")
    authorisation_id: str | None = Field(
        default=None,
        description="The governance verdict that authorised this, where one exists. The "
        "record names it; it does not re-check it -- that is the authoriser's job, and "
        "claiming otherwise here would be a check that inspected nothing.",
    )
    notes: str = ""


class EvidenceRef(_Model):
    """Digests of what the decision rested on, so a replay can be shown to match."""

    outcome_kind: Literal["result", "refusal", "apparatus_failure"]
    result_digest: str | None = None
    graph_digest: str | None = None
    data_digest: str | None = None
    query_digest: str | None = None
    contract_version: str | None = None
    core_version: str | None = None
    model: ModelRef | None = Field(
        default=None,
        description="The registered causal model version the analysis used, when it used one "
        "(causal_decision.assumptions). None means the graph arrived inline and nothing but "
        "graph_digest records which one it was.",
    )


class DecisionRecord(_Model):
    """One link. ``entry_digest`` covers every field above it, including ``prev_digest``."""

    sequence: int = Field(ge=0)
    recorded_at: str = Field(description="ISO 8601, from the caller's clock.")
    decision: Decision
    evidence: EvidenceRef
    approval: Approval | None = None
    executed: bool = Field(
        default=False,
        description="Whether the action was actually taken. A decision to act and an "
        "action taken are different events, and conflating them is how a ledger comes "
        "to overstate what happened.",
    )
    review_by: str | None = Field(
        default=None,
        description="ISO 8601, from the caller's clock: when this verdict stops standing on its "
        "own. By then a follow-up should have measured it, or the question been decided again. "
        "Evidence describes the population and conditions it was drawn from, and those drift; "
        "a decision with no date claims to hold forever. None on records sealed before the "
        "field existed.",
    )
    prev_digest: str
    entry_digest: str = ""

    @model_validator(mode="after")
    def _review_after_recording(self) -> DecisionRecord:
        if self.review_by is None:
            return self
        review_by = _parse(self.review_by, "review_by")
        recorded = _parse(self.recorded_at, "recorded_at")
        if (review_by.tzinfo is None) != (recorded.tzinfo is None):
            raise ValueError("review_by and recorded_at must both carry a UTC offset, or neither")
        if review_by <= recorded:
            raise ValueError(f"review_by {self.review_by} is not after recorded_at "
                             f"{self.recorded_at}")
        return self

    def content(self) -> dict[str, object]:
        """The bytes the digest covers: everything except the digest itself.

        ``evidence.model`` and ``review_by`` are left out when they are None, so every
        record sealed before those fields existed digests exactly as it did then
        (pinned by tests/fixtures/ledger_chain_before_model_ref.json). A contract may
        grow; the records already in a ledger must keep verifying.
        """
        out = self.model_dump(mode="json", exclude={"entry_digest"})
        evidence = out["evidence"]
        if isinstance(evidence, dict) and evidence.get("model") is None:
            evidence.pop("model", None)
        if out.get("review_by") is None:
            out.pop("review_by", None)
        return out

    def sealed(self) -> DecisionRecord:
        return self.model_copy(update={"entry_digest": digest(self.content())})


class ChainReport(_Model):
    """Whether a chain holds, and if not, exactly where it stops holding."""

    ok: bool
    length: int
    head_digest: str | None = None
    broken_at: int | None = None
    reason: str = ""


def _evidence_of(outcome: object, model: ModelRef | None = None) -> EvidenceRef:
    prov = getattr(outcome, "provenance", None)
    kind = getattr(outcome, "kind", None)
    if kind not in ("result", "refusal", "apparatus_failure"):
        raise TypeError(f"not a causal outcome: {type(outcome).__name__}")
    return EvidenceRef(
        outcome_kind=kind,
        result_digest=getattr(outcome, "result_digest", None),
        graph_digest=getattr(prov, "graph_digest", None),
        data_digest=getattr(prov, "data_digest", None),
        query_digest=getattr(prov, "query_digest", None),
        contract_version=getattr(prov, "contract_version", None),
        core_version=getattr(prov, "core_version", None),
        model=model,
    )


def append(
    tail: DecisionRecord | None,
    decision: Decision,
    outcome: object,
    recorded_at: str,
    approval: Approval | None = None,
    executed: bool = False,
    model: ModelRef | None = None,
    review_by: str | None = None,
) -> DecisionRecord:
    """Seal a new entry onto the chain whose last entry is ``tail``.

    ``model`` names the registered model version the analysis ran on. It sits beside
    ``graph_digest``, which covers the graph the kernel actually analysed (a model
    node with no data column becomes latent there, and refuted edges are dropped), so
    the two digests differ by design; the caller is trusted to pass the version it
    analysed with, and the backend does so from the same object.
    """
    return DecisionRecord(
        sequence=0 if tail is None else tail.sequence + 1,
        recorded_at=recorded_at,
        decision=decision,
        evidence=_evidence_of(outcome, model),
        approval=approval,
        executed=executed,
        review_by=review_by,
        prev_digest=GENESIS if tail is None else tail.entry_digest,
    ).sealed()


def _parse(stamp: str, name: str) -> datetime:
    try:
        return datetime.fromisoformat(stamp)
    except ValueError as exc:
        raise ValueError(f"{name} is not ISO 8601: {stamp!r}") from exc


class PastReview(_Model):
    """A decision whose review date has passed with nothing re-checking it."""

    entry_digest: str
    sequence: int
    action: str
    verdict: Verdict
    review_by: str
    executed: bool


def past_review(
    records: list[DecisionRecord], measured: Collection[str], as_of: str
) -> list[PastReview]:
    """Decisions still standing on evidence nobody has re-checked since their review date.

    A record leaves this list in one of two ways: a follow-up measured it (its entry
    digest is in ``measured``), or a later record decided the same action again --
    the newer verdict is the one that stands. Refusals are not listed: they
    recommended nothing, so nothing rests on them. Records without a review date are
    not listed either; they predate the field, and inventing a date for them here
    would put a claim in the report that nobody made.
    """
    now = _parse(as_of, "as_of")
    latest: dict[str, int] = {}
    for r in records:
        latest[r.decision.request.action] = max(
            latest.get(r.decision.request.action, -1), r.sequence
        )
    out = []
    for r in records:
        if (
            r.review_by is None
            or r.decision.verdict is Verdict.refuse
            or r.entry_digest in measured
            or latest[r.decision.request.action] != r.sequence
        ):
            continue
        due = _parse(r.review_by, "review_by")
        if (due.tzinfo is None) != (now.tzinfo is None):
            raise ValueError("as_of and review_by must both carry a UTC offset, or neither")
        if due < now:
            out.append(PastReview(
                entry_digest=r.entry_digest,
                sequence=r.sequence,
                action=r.decision.request.action,
                verdict=r.decision.verdict,
                review_by=r.review_by,
                executed=r.executed,
            ))
    return out


def verify_chain(records: list[DecisionRecord]) -> ChainReport:
    """Recompute every digest and every link.

    Catches an altered entry (its digest no longer matches its content), a removed
    or reordered one (the links no longer join), and a chain that does not start at
    the genesis value.
    """
    if not records:
        return ChainReport(ok=True, length=0, reason="empty chain")

    expected_prev = GENESIS
    for i, rec in enumerate(records):
        if rec.sequence != i:
            return ChainReport(
                ok=False, length=len(records), broken_at=i,
                reason=f"entry {i} claims sequence {rec.sequence}: an entry is missing "
                "or reordered",
            )
        if rec.prev_digest != expected_prev:
            return ChainReport(
                ok=False, length=len(records), broken_at=i,
                reason=f"entry {i} points at {rec.prev_digest[:23]}..., but the entry before it "
                f"digests to {expected_prev[:23]}...: the chain was broken here",
            )
        recomputed = digest(rec.content())
        if recomputed != rec.entry_digest:
            return ChainReport(
                ok=False, length=len(records), broken_at=i,
                reason=f"entry {i} does not match its own digest: its content was altered after "
                "it was recorded",
            )
        expected_prev = rec.entry_digest

    return ChainReport(ok=True, length=len(records), head_digest=records[-1].entry_digest)


class Checkpoint(_Model):
    """A chain's head at one moment, to be published somewhere its writer cannot rewrite.

    Publishing is the deployment's job -- a transparency log, a countersigning party, a
    signed commit in another repository, an email to an auditor. What this module can
    do is say exactly what to publish, and check a chain against what was published.
    """

    sequence: int = Field(ge=0)
    entry_digest: str
    length: int = Field(ge=1)


class AnchorReport(_Model):
    ok: bool
    checked: int
    chain: ChainReport
    broken_at: int | None = Field(
        default=None, description="The sequence of the first checkpoint the chain contradicts."
    )
    reason: str = ""


def checkpoint(records: list[DecisionRecord]) -> Checkpoint:
    """The head to publish. Refuses an empty or internally broken chain."""
    report = verify_chain(records)
    if not records or not report.ok:
        raise ValueError(f"no checkpoint for this chain: {report.reason or 'it is empty'}")
    head = records[-1]
    return Checkpoint(sequence=head.sequence, entry_digest=head.entry_digest,
                      length=len(records))


def verify_against(records: list[DecisionRecord], checkpoints: list[Checkpoint]) -> AnchorReport:
    """Whether the chain still contains every published checkpoint.

    ``verify_chain`` alone passes a chain rewritten from an altered entry onward,
    because every digest after the edit is recomputed. A checkpoint published before
    the rewrite names a digest the rewritten chain no longer has at that sequence --
    that is the difference between detecting tampering and evidencing it.
    """
    report = verify_chain(records)
    if not report.ok:
        return AnchorReport(ok=False, checked=0, chain=report, reason=report.reason)
    by_seq = {r.sequence: r for r in records}
    for i, cp in enumerate(sorted(checkpoints, key=lambda c: c.sequence)):
        rec = by_seq.get(cp.sequence)
        if rec is None:
            return AnchorReport(
                ok=False, checked=i, chain=report, broken_at=cp.sequence,
                reason=f"a checkpoint names entry {cp.sequence}, which the chain no longer has: "
                "entries were removed after it was published",
            )
        if rec.entry_digest != cp.entry_digest:
            return AnchorReport(
                ok=False, checked=i, chain=report, broken_at=cp.sequence,
                reason=f"entry {cp.sequence} digests to {rec.entry_digest[:23]}..., but the "
                f"checkpoint published for it says {cp.entry_digest[:23]}...: the chain was "
                "rewritten after the checkpoint was published",
            )
    return AnchorReport(ok=True, checked=len(checkpoints), chain=report)


def replays(record: DecisionRecord, decision: Decision) -> bool:
    """Whether re-deciding produced the same verdict, byte for byte.

    The decision is a pure function of (outcome, request, policy) and carries a
    digest of all three, so a recorded decision can be re-run and compared. This is
    what makes the record disputable rather than merely stored.
    """
    return bool(
        decision.decision_digest
        and record.decision.decision_digest
        and decision.decision_digest == record.decision.decision_digest
    )
