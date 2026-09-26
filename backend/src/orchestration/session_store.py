"""Durable session-state persistence for EpistemicState snapshots.

This closes the gap between the documented session-memory tier and the earlier
in-process-only cache by persisting the latest EpistemicState per session in the
platform SQL store. The API still keeps a hot in-memory cache for low-latency
resume/read paths, but durable recovery now survives process restarts.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import structlog
from sqlalchemy import text

from src.db.factory import CloudSqlFactory
from src.db.types import TIMESTAMP, typed_text
from src.semantic_layer.models import EpistemicState

log = structlog.get_logger(__name__)


class SessionStateStore:
    """Read/write the latest EpistemicState snapshot for a session."""

    async def load(self, session_id: str) -> EpistemicState | None:
        """Return the latest persisted state for ``session_id`` if present."""
        found = await self.load_with_owner(session_id)
        return None if found is None else found[0]

    async def load_with_owner(self, session_id: str) -> tuple[EpistemicState, str | None] | None:
        """The latest persisted state and the id of the user who started the session.

        The owner is ``None`` for a session saved without one; callers that serve
        a user decide who may read it (src/middleware/rbac.py may_use_session)."""
        async with CloudSqlFactory.get_session_factory()() as session:
            row = await session.execute(
                text("SELECT state_json, user_id FROM session_state WHERE session_id = :sid"),
                {"sid": session_id},
            )
            record = row.mappings().one_or_none()

        if record is None:
            return None

        payload = record["state_json"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        return EpistemicState.model_validate(payload), record["user_id"]

    async def save(self, state: EpistemicState, user_id: str | None = None) -> None:
        """Upsert the latest EpistemicState snapshot for the session.

        ``user_id`` is recorded when the session's row is created and never
        rewritten: a later save by anyone else -- a governance role resuming it --
        leaves the owner in place."""
        now = datetime.now(UTC)
        payload = json.dumps(state.model_dump(mode="json"), sort_keys=True)

        async with CloudSqlFactory.get_session_factory()() as session:
            await session.execute(
                typed_text(
                    "INSERT INTO session_state "
                    "(session_id, user_id, agent_id, state_json, created_at, updated_at) "
                    "VALUES (:sid, :uid, :aid, :state_json, :created_at, :updated_at) "
                    "ON CONFLICT(session_id) DO UPDATE SET "
                    "agent_id = excluded.agent_id, "
                    "state_json = excluded.state_json, "
                    "updated_at = excluded.updated_at",
                    created_at=TIMESTAMP,
                    updated_at=TIMESTAMP,
                ),
                {
                    "sid": state.session_id,
                    "uid": user_id,
                    "aid": state.agent_id,
                    "state_json": payload,
                    "created_at": now,
                    "updated_at": now,
                },
            )
            await session.commit()

        log.debug(
            "session_store.saved",
            session_id=state.session_id,
            agent_id=state.agent_id,
        )
