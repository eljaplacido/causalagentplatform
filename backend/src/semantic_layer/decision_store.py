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
from typing import TYPE_CHECKING, Any

import structlog
from causal_decision.ledger import ChainReport, DecisionRecord, append, verify_chain
from sqlalchemy import text

from src.db.factory import CloudSqlFactory

if TYPE_CHECKING:
    from causal_decision.contract import Decision
    from causal_decision.ledger import Approval

log = structlog.get_logger(__name__)


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
