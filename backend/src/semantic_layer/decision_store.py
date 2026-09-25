"""
Persistence for the decision ledger.

`causal_decision.ledger` builds and verifies the chain; it has no store, no clock
and no network, so that the chain can be rebuilt and re-checked anywhere. This is
the platform's half: append a sealed record, read them back, and verify by
*recomputing* rather than by trusting what the rows say about themselves.

Two rules the store holds to:

  * **Append only.** A row is inserted once. The sequence is the primary key, so a
    second attempt at the same position fails rather than overwriting; the
    production schema also revokes UPDATE and DELETE and traps both in a trigger.
  * **Verify from the record.** Every row carries the sealed entry as JSON plus a
    few columns to walk and filter by. Those columns are an index over the record,
    never a source of truth: verification parses the record and recomputes the
    digests, so a row whose columns were edited to say something else does not
    verify.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import structlog
from causal_decision.ledger import ChainReport, DecisionRecord, append, verify_chain
from causal_decision.outcome import FollowUp, Realization
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from src.db.factory import CloudSqlFactory

if TYPE_CHECKING:
    from causal_decision.assumptions import ModelRef
    from causal_decision.contract import Decision
    from causal_decision.ledger import Approval

log = structlog.get_logger(__name__)


class FollowUpConflictError(RuntimeError):
    """This decision already has a plan, or already has its measurement.

    The first measurement stands. A second one would join the calibration average
    and let a caller outvote a disappointing window with a friendlier one.
    """


class LedgerConflictError(RuntimeError):
    """Another writer sealed an entry at this position first.

    The chain is a line, not a set: two decisions recorded concurrently cannot both
    be entry N. The loser retries against the new head rather than overwriting, so
    the losing decision is delayed, never lost.
    """


class DecisionStore:
    """Reads and appends the decision chain."""

    async def head(self) -> DecisionRecord | None:
        async with CloudSqlFactory.get_session_factory()() as session:
            row = await session.execute(
                text("SELECT record FROM decision_records ORDER BY sequence DESC LIMIT 1")
            )
            raw = row.scalar()
        return None if raw is None else DecisionRecord.model_validate(_loads(raw))

    async def record(
        self,
        decision: Decision,
        outcome: object,
        recorded_at: str,
        approval: Approval | None = None,
        executed: bool = False,
        model: ModelRef | None = None,
    ) -> DecisionRecord:
        """Seal this decision onto the end of the chain and store it."""
        tail = await self.head()
        entry = append(
            tail,
            decision,
            outcome,
            recorded_at=recorded_at,
            approval=approval,
            executed=executed,
            model=model,
        )
        async with CloudSqlFactory.get_session_factory()() as session:
            try:
                await session.execute(
                    text(
                        "INSERT INTO decision_records "
                        "(sequence, entry_digest, prev_digest, decision_digest, subject, "
                        " verdict, recorded_at, record) "
                        "VALUES (:seq, :entry, :prev, :dd, :subject, :verdict, :at, :record)"
                    ),
                    {
                        "seq": entry.sequence,
                        "entry": entry.entry_digest,
                        "prev": entry.prev_digest,
                        "dd": entry.decision.decision_digest,
                        "subject": entry.decision.request.action,
                        "verdict": str(entry.decision.verdict),
                        "at": entry.recorded_at,
                        "record": json.dumps(entry.model_dump(mode="json")),
                    },
                )
                await session.commit()
            except Exception as exc:  # noqa: BLE001 - re-raised as a typed conflict below
                await session.rollback()
                raise LedgerConflictError(
                    f"could not seal decision at sequence {entry.sequence}: {exc}"
                ) from exc

        log.info(
            "ledger.recorded",
            sequence=entry.sequence,
            verdict=str(entry.decision.verdict),
            entry_digest=entry.entry_digest,
        )
        return entry

    async def all_records(self, limit: int | None = None) -> list[DecisionRecord]:
        sql = "SELECT record FROM decision_records ORDER BY sequence ASC"
        params: dict[str, Any] = {}
        if limit is not None:
            sql += " LIMIT :limit"
            params["limit"] = limit
        async with CloudSqlFactory.get_session_factory()() as session:
            rows = await session.execute(text(sql), params)
            raws = [r[0] for r in rows.all()]
        return [DecisionRecord.model_validate(_loads(r)) for r in raws]

    async def verify(self) -> ChainReport:
        """Recompute the whole chain from the stored records."""
        return verify_chain(await self.all_records())

    async def for_subject(self, action: str, limit: int = 50) -> list[DecisionRecord]:
        async with CloudSqlFactory.get_session_factory()() as session:
            rows = await session.execute(
                text(
                    "SELECT record FROM decision_records WHERE subject = :s "
                    "ORDER BY sequence DESC LIMIT :limit"
                ),
                {"s": action, "limit": limit},
            )
            raws = [r[0] for r in rows.all()]
        return [DecisionRecord.model_validate(_loads(r)) for r in raws]

    # ── follow-ups: what was going to be re-measured, and what came back ──────

    async def save_plan(self, entry_digest: str, followup: FollowUp) -> None:
        """Record, at decision time, what would have to be re-run to score this verdict.

        Written now rather than reconstructed later, because the plan carries a
        copy of the prediction the verdict used. Rebuilt afterwards it would carry
        whatever a fresh analysis says today, which is the target moving after the
        result is known.
        """
        await self._append_followup(entry_digest, followup.decision_digest, "plan", None, followup)

    async def plan_for(self, entry_digest: str) -> FollowUp | None:
        async with CloudSqlFactory.get_session_factory()() as session:
            row = await session.execute(
                text(
                    "SELECT payload FROM decision_followups "
                    "WHERE entry_digest = :d AND kind = 'plan' ORDER BY id ASC LIMIT 1"
                ),
                {"d": entry_digest},
            )
            raw = row.scalar()
        return None if raw is None else FollowUp.model_validate(_loads(raw))

    async def save_realization(self, entry_digest: str, realization: Realization) -> None:
        await self._append_followup(
            entry_digest,
            realization.decision_digest,
            "realization",
            realization.decidable,
            realization,
        )

    async def realizations(self, limit: int | None = None) -> list[Realization]:
        """Every measurement taken, decidable or not.

        Undecidable ones come back with the rest. They are not missing at random --
        an action rolled out to everybody is precisely the one whose effect can no
        longer be estimated -- so filtering them here would quietly select the
        flattering subset.
        """
        # With a limit, the most recent N -- "LIMIT" on an ascending scan returned
        # the oldest, which is never what a caller asking for a window means.
        sql = (
            "SELECT payload FROM decision_followups WHERE kind = 'realization' ORDER BY id DESC"
        )
        params: dict[str, Any] = {}
        if limit is not None:
            sql += " LIMIT :limit"
            params["limit"] = limit
        async with CloudSqlFactory.get_session_factory()() as session:
            rows = await session.execute(text(sql), params)
            raws = [r[0] for r in rows.all()]
        return [Realization.model_validate(_loads(r)) for r in reversed(raws)]

    async def has_realization(self, entry_digest: str) -> bool:
        async with CloudSqlFactory.get_session_factory()() as session:
            row = await session.execute(
                text(
                    "SELECT 1 FROM decision_followups "
                    "WHERE entry_digest = :d AND kind = 'realization' LIMIT 1"
                ),
                {"d": entry_digest},
            )
            return row.scalar() is not None

    async def _append_followup(
        self,
        entry_digest: str,
        decision_digest: str,
        kind: str,
        decidable: bool | None,
        payload: FollowUp | Realization,
    ) -> None:
        async with CloudSqlFactory.get_session_factory()() as session:
            try:
                await session.execute(
                    text(
                        "INSERT INTO decision_followups ("
                    "  entry_digest, decision_digest, kind, decidable, created_at, payload"
                        ") VALUES (:e, :d, :k, :ok, :at, :p)"
                    ),
                    {
                        "e": entry_digest,
                        "d": decision_digest,
                        "k": kind,
                        "ok": decidable,
                        "at": datetime.now(UTC).isoformat(),
                        "p": payload.model_dump_json(),
                    },
                )
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise FollowUpConflictError(
                    f"decision {entry_digest} already has a {kind}; the first one stands"
                ) from exc
        log.info(
            "decision_store.followup_appended",
            entry_digest=entry_digest,
            kind=kind,
            decidable=decidable,
        )

    async def by_digest(self, entry_digest: str) -> DecisionRecord | None:
        async with CloudSqlFactory.get_session_factory()() as session:
            row = await session.execute(
                text("SELECT record FROM decision_records WHERE entry_digest = :d"),
                {"d": entry_digest},
            )
            raw = row.scalar()
        return None if raw is None else DecisionRecord.model_validate(_loads(raw))


def _loads(raw: Any) -> dict[str, Any]:
    """JSONB comes back as a dict on Postgres and as text on SQLite."""
    if isinstance(raw, str):
        return dict(json.loads(raw))
    if isinstance(raw, dict):
        return raw
    raise TypeError(f"decision record column has unexpected type {type(raw).__name__}")
