"""Shadow decisions: sealed like any other, never reported as a decision, never acted on.

A shadow run (shadow/README.md) measures the platform on real questions without
risk: the platform analyses, rules and seals its verdict, nobody acts on it, and
after a fixed window the same analysis is re-run on the later data and the sealed
prediction is scored. For that to be a measurement and not a rehearsal, two things
must hold at runtime:

- **A shadow verdict is never emitted.** The second brain (agentcenter) receives
  recorded decisions as ``causalplatform.decision.recorded`` events and may act on
  them; a shadow verdict that reached it would be a real recommendation wearing a
  tag. :func:`emittable` is the one gate every emitter passes a record through, and
  it refuses a record whose sealed ``shadow`` tag is set.
- **A shadow verdict is kept apart from the real ones.** It sits in the same hash
  chain (one line of what the platform concluded, in order), told apart by the tag,
  which is sealed into the entry's digest. :func:`in_scope` is the filter the
  listings use, so a shadow round never inflates the count of real decisions, never
  joins the real calibration report, and never retires a real verdict in
  :func:`causal_decision.ledger.past_review`.

Follow-ups are taken exactly as for a real decision: the plan is sealed at
decision time and the re-run is scored by :mod:`causal_decision.outcome`. Running
and scoring a whole round is :mod:`causal_decision.shadow_round`.

Everything here is pure: no clock, no store, no network.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Literal

from causal_decision.ledger import DecisionRecord

#: The event a recorded decision is reported to the second brain as. A shadow
#: decision is never sent under it (nor under any other kind).
DECISION_EVENT = "causalplatform.decision.recorded"

#: Which decisions a listing covers: real ones (the default everywhere), shadow ones,
#: or both.
Scope = Literal["real", "shadow", "all"]


class ShadowEmissionError(RuntimeError):
    """A shadow decision was about to be reported as a decision.

    Raised rather than logged and skipped by :func:`emittable`, so a caller that
    forgot to check cannot send it by accident; an emitter that must never raise
    catches this and does not send.
    """


def is_shadow(record: DecisionRecord) -> bool:
    """Whether this record was sealed as a shadow decision."""
    return record.shadow is not None


def emittable(record: DecisionRecord) -> DecisionRecord:
    """The record, if it may be reported as a decision; raises for a shadow one.

    Every path that reports a decision outward goes through this with the sealed
    record itself -- not with a payload built from it, which could drop the tag.
    """
    if record.shadow is not None:
        raise ShadowEmissionError(
            f"decision {record.entry_digest[:23]}... was recorded in shadow "
            f"({record.shadow!r}): a shadow verdict is never reported as a decision, "
            "and nobody acts on it because of the platform"
        )
    return record


def in_scope(record: DecisionRecord, scope: Scope = "real", tag: str | None = None) -> bool:
    """Whether a record belongs in a listing of ``scope`` (and of shadow ``tag``, if given).

    A ``tag`` names one shadow round, so it selects shadow records only, whatever
    the scope says about real ones.
    """
    if tag is not None:
        return record.shadow == tag
    if scope == "real":
        return record.shadow is None
    if scope == "shadow":
        return record.shadow is not None
    return True


def select(
    records: Iterable[DecisionRecord], scope: Scope = "real", tag: str | None = None
) -> list[DecisionRecord]:
    """The records of ``scope`` (and ``tag``), in the order given."""
    return [r for r in records if in_scope(r, scope, tag)]
