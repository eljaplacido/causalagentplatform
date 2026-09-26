"""
Durable, append-only provenance (ADR-002 Part A).

Tables ``provenance_entries`` and ``provenance_erasures`` (migration
``011_provenance.sql``; the SQLite rendering is in ``src/db/factory.py``). Before
this module the provenance API kept its records in two module-level dicts, lost
on restart and erased by a DELETE route.

Two rules the store holds to:

  * **Append only.** It has no method that updates or deletes a row, and the
    schema refuses both: PostgreSQL grants neither to any role and traps them in a
    trigger, SQLite traps them in a trigger.
  * **Erasure is a tombstone.** `erase` inserts a row saying which session, who,
    when, why, and up to which ``sequence`` that session's entries are hidden.
    `read` leaves those entries out and returns the tombstone in their place. The
    hidden rows stay in the database -- this hides content from the API, it is
    not erasure in the GDPR Art. 17 sense (register ``gdpr-controls``).

A write can join a caller's transaction (``session=``), so a record that must
exist exactly when something else does -- the Guardian's activity and its
audit_log row -- commits or rolls back with it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

import structlog
from sqlalchemy import text

from src.db.factory import CloudSqlFactory
from src.db.types import TIMESTAMP, json_value, timestamp_text, typed_text
from src.semantic_layer.provenance import LLMInvocation, ProvenanceRecord

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.ext.asyncio import AsyncSession

log = structlog.get_logger(__name__)

#: A reason shorter than this is refused (the schema's CHECK says the same).
MIN_REASON_LENGTH = 10


class ProvenanceWriteError(RuntimeError):
    """A provenance entry could not be stored. The flow that produced it fails."""


@dataclass(frozen=True)
class Erasure:
    """A tombstone: the fact of an erasure, kept after its content is hidden."""

    id: UUID
    session_id: str
    hides_through: int
    hidden_count: int
    erased_by: str
    reason: str
    erased_at: str

    def as_json(self) -> dict[str, Any]:
        return {
            "id": str(self.id),
            "session_id": self.session_id,
            "hides_through": self.hides_through,
            "hidden_count": self.hidden_count,
            "erased_by": self.erased_by,
            "reason": self.reason,
            "erased_at": self.erased_at,
        }


@dataclass(frozen=True)
class ProvenanceView:
    """What the API may show of a session: visible entries and every tombstone."""

    session_id: str
    records: list[ProvenanceRecord]
    invocations: list[LLMInvocation]
    erasures: list[Erasure]


_INSERT_ENTRY = typed_text(
    "INSERT INTO provenance_entries (id, kind, session_id, agent_id, recorded_at, body) "
    "VALUES (:id, :kind, :sid, :aid, :at, :body)",
    at=TIMESTAMP,
)


def _entry_params(kind: str, item: ProvenanceRecord | LLMInvocation) -> dict[str, Any]:
    return {
        "id": str(item.id),
        "kind": kind,
        "sid": item.session_id,
        "aid": item.agent_id,
        "at": item.timestamp,
        "body": json.dumps(item.model_dump(mode="json"), sort_keys=True),
    }


class ProvenanceStore:
    """Appends provenance entries and tombstones; reads what is not hidden."""

    async def append(
        self,
        records: Sequence[ProvenanceRecord] = (),
        invocations: Sequence[LLMInvocation] = (),
        *,
        session: AsyncSession | None = None,
    ) -> None:
        """Insert every entry, in order, in one transaction.

        With `session`, the inserts join the caller's transaction and the caller
        commits. Without it, the store opens and commits its own. Either way a
        failure raises `ProvenanceWriteError`: provenance that silently fails to
        be written would read as a complete trace.
        """
        params = [_entry_params("record", r) for r in records]
        params += [_entry_params("invocation", i) for i in invocations]
        if not params:
            return
        try:
            if session is not None:
                for p in params:
                    await session.execute(_INSERT_ENTRY, p)
                return
            async with CloudSqlFactory.get_session_factory()() as own:
                for p in params:
                    await own.execute(_INSERT_ENTRY, p)
                await own.commit()
        except Exception as exc:
            log.error("provenance.write_failed", error=str(exc), entries=len(params))
            raise ProvenanceWriteError(f"provenance write failed: {exc}") from exc

    async def read(self, session_id: str) -> ProvenanceView:
        """The session's entries not hidden by a tombstone, and its tombstones."""
        async with CloudSqlFactory.get_session_factory()() as session:
            erasures = await self._erasures(session, session_id)
            watermark = max((e.hides_through for e in erasures), default=0)
            rows = (
                await session.execute(
                    text(
                        "SELECT kind, body FROM provenance_entries "
                        "WHERE session_id = :sid AND sequence > :watermark "
                        "ORDER BY sequence"
                    ),
                    {"sid": session_id, "watermark": watermark},
                )
            ).all()
        records: list[ProvenanceRecord] = []
        invocations: list[LLMInvocation] = []
        for kind, body in rows:
            payload = json_value(body)
            if kind == "record":
                records.append(ProvenanceRecord.model_validate(payload))
            else:
                invocations.append(LLMInvocation.model_validate(payload))
        return ProvenanceView(session_id, records, invocations, erasures)

    async def erase(self, session_id: str, *, erased_by: str, reason: str) -> Erasure:
        """Hide the session's entries written so far, and record that it was done.

        Entries written after the tombstone are visible again: a later run under
        the same session id is new evidence, not the erased one. Nothing is
        deleted.
        """
        if len(reason.strip()) < MIN_REASON_LENGTH:
            raise ValueError(
                f"an erasure needs a reason of at least {MIN_REASON_LENGTH} characters"
            )
        if not erased_by.strip():
            raise ValueError("an erasure needs the identity of whoever erased")
        erasure_id = uuid4()
        now = datetime.now(UTC)
        async with CloudSqlFactory.get_session_factory()() as session:
            previous = max(
                (e.hides_through for e in await self._erasures(session, session_id)),
                default=0,
            )
            top, count = (
                await session.execute(
                    text(
                        "SELECT COALESCE(MAX(sequence), 0), COUNT(*) FROM provenance_entries "
                        "WHERE session_id = :sid AND sequence > :previous"
                    ),
                    {"sid": session_id, "previous": previous},
                )
            ).one()
            hides_through = max(int(top), previous)
            await session.execute(
                typed_text(
                    "INSERT INTO provenance_erasures "
                    "(id, session_id, hides_through, hidden_count, erased_by, reason, erased_at) "
                    "VALUES (:id, :sid, :through, :count, :by, :reason, :at)",
                    at=TIMESTAMP,
                ),
                {
                    "id": str(erasure_id),
                    "sid": session_id,
                    "through": hides_through,
                    "count": int(count),
                    "by": erased_by,
                    "reason": reason,
                    "at": now,
                },
            )
            await session.commit()
        log.info(
            "provenance.erased",
            session_id=session_id,
            erased_by=erased_by,
            hidden_count=int(count),
        )
        return Erasure(
            id=erasure_id,
            session_id=session_id,
            hides_through=hides_through,
            hidden_count=int(count),
            erased_by=erased_by,
            reason=reason,
            erased_at=now.isoformat(),
        )

    @staticmethod
    async def _erasures(session: AsyncSession, session_id: str) -> list[Erasure]:
        rows = (
            await session.execute(
                text(
                    "SELECT id, session_id, hides_through, hidden_count, erased_by, reason, "
                    "erased_at FROM provenance_erasures WHERE session_id = :sid "
                    "ORDER BY hides_through, erased_at"
                ),
                {"sid": session_id},
            )
        ).mappings().all()
        return [
            Erasure(
                id=UUID(str(r["id"])),
                session_id=r["session_id"],
                hides_through=int(r["hides_through"]),
                hidden_count=int(r["hidden_count"]),
                erased_by=r["erased_by"],
                reason=r["reason"],
                erased_at=timestamp_text(r["erased_at"]) or "",
            )
            for r in rows
        ]
