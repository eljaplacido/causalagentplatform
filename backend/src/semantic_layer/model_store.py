"""
The assumption registry's store: causal model versions, and the decisions made on them.

Pure rules live in ``causal_decision.assumptions`` -- who may review what, what a
revision may and may not do, what a version's digest covers. This module only
keeps the rows and holds one rule the pure layer cannot: that a new version is
appended to the version it was built from. Two writers revising the same head
concurrently cannot both become version N+1; the loser gets a conflict and
rebuilds on the new head, so a revision is delayed, never silently merged.
"""

from __future__ import annotations

import json
from typing import Any

import structlog
from causal_core.contract import CausalQuery
from causal_decision.assumptions import HistoryReport, ModelRef, ModelVersion, verify_history
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from src.db.factory import CloudSqlFactory

log = structlog.get_logger(__name__)


class ModelConflictError(RuntimeError):
    """The version was built on a head that is no longer the head, or the name is taken."""


class DecisionOnModel(BaseModel):
    """A decision recorded against a registered model version, with the query it asked."""

    entry_digest: str
    model_version: int
    model_digest: str
    verdict: str
    action: str
    query: CausalQuery
    unmeasured: tuple[str, ...] = ()
    recorded_at: str


def _loads(raw: Any) -> dict[str, Any]:
    """JSONB comes back as a dict on Postgres and as text on SQLite."""
    if isinstance(raw, str):
        return dict(json.loads(raw))
    if isinstance(raw, dict):
        return raw
    raise TypeError(f"unexpected JSON payload type {type(raw).__name__}")


class ModelStore:
    async def head(self, name: str) -> ModelVersion | None:
        async with CloudSqlFactory.get_session_factory()() as session:
            row = await session.execute(
                text(
                    "SELECT record FROM causal_model_versions WHERE name = :n "
                    "ORDER BY version DESC LIMIT 1"
                ),
                {"n": name},
            )
            raw = row.scalar()
        return None if raw is None else ModelVersion.model_validate(_loads(raw))

    async def get(self, name: str, version: int) -> ModelVersion | None:
        async with CloudSqlFactory.get_session_factory()() as session:
            row = await session.execute(
                text("SELECT record FROM causal_model_versions WHERE name = :n AND version = :v"),
                {"n": name, "v": version},
            )
            raw = row.scalar()
        return None if raw is None else ModelVersion.model_validate(_loads(raw))

    async def history(self, name: str) -> list[ModelVersion]:
        async with CloudSqlFactory.get_session_factory()() as session:
            rows = await session.execute(
                text(
                    "SELECT record FROM causal_model_versions WHERE name = :n ORDER BY version ASC"
                ),
                {"n": name},
            )
            raws = [r[0] for r in rows.all()]
        return [ModelVersion.model_validate(_loads(r)) for r in raws]

    async def heads(self) -> list[ModelVersion]:
        async with CloudSqlFactory.get_session_factory()() as session:
            rows = await session.execute(
                text(
                    "SELECT m.record FROM causal_model_versions m JOIN ("
                    "  SELECT name, MAX(version) AS v FROM causal_model_versions GROUP BY name"
                    ") h ON m.name = h.name AND m.version = h.v ORDER BY m.name"
                )
            )
            raws = [r[0] for r in rows.all()]
        return [ModelVersion.model_validate(_loads(r)) for r in raws]

    async def verify(self, name: str) -> HistoryReport:
        return verify_history(await self.history(name))

    async def append(self, version: ModelVersion) -> ModelVersion:
        """Store ``version`` if and only if its parent is still the head."""
        head = await self.head(version.name)
        if version.version == 1 and head is not None:
            raise ModelConflictError(f"a model named {version.name!r} already exists")
        if version.version > 1 and (head is None or head.digest != version.parent_digest):
            raise ModelConflictError(
                f"{version.name} v{version.version} was built on "
                f"{(version.parent_digest or '')[:19]}..., but the head is "
                f"{'nothing' if head is None else f'v{head.version}'}: rebuild on the head"
            )
        async with CloudSqlFactory.get_session_factory()() as session:
            try:
                await session.execute(
                    text(
                        "INSERT INTO causal_model_versions "
                        "(digest, name, version, parent_digest, author_id, created_at, record) "
                        "VALUES (:d, :n, :v, :p, :a, :at, :r)"
                    ),
                    {
                        "d": version.digest,
                        "n": version.name,
                        "v": version.version,
                        "p": version.parent_digest,
                        "a": version.author_id,
                        "at": version.created_at,
                        "r": version.model_dump_json(),
                    },
                )
                await session.commit()
            except IntegrityError as exc:  # (name, version) taken by a concurrent writer
                await session.rollback()
                raise ModelConflictError(
                    f"{version.name} v{version.version} was written concurrently: rebuild on "
                    "the head"
                ) from exc
        log.info(
            "model_store.appended",
            name=version.name,
            version=version.version,
            digest=version.digest,
            author_id=version.author_id,
        )
        return version

    # ── decisions made on a version ───────────────────────────────────────────

    async def record_decision(
        self,
        entry_digest: str,
        model: ModelRef,
        *,
        verdict: str,
        action: str,
        query: CausalQuery,
        unmeasured: tuple[str, ...],
        recorded_at: str,
    ) -> None:
        async with CloudSqlFactory.get_session_factory()() as session:
            await session.execute(
                text(
                    "INSERT INTO decision_model_refs (entry_digest, model_name, model_version, "
                    " model_digest, verdict, action, query, unmeasured, recorded_at) "
                    "VALUES (:e, :n, :v, :d, :verdict, :action, :q, :u, :at)"
                ),
                {
                    "e": entry_digest,
                    "n": model.name,
                    "v": model.version,
                    "d": model.digest,
                    "verdict": verdict,
                    "action": action,
                    "q": query.model_dump_json(),
                    "u": json.dumps(list(unmeasured)),
                    "at": recorded_at,
                },
            )
            await session.commit()

    async def decisions_on(self, name: str, before_version: int | None = None
                           ) -> list[DecisionOnModel]:
        sql = (
            "SELECT entry_digest, model_version, model_digest, verdict, action, query, "
            "unmeasured, recorded_at FROM decision_model_refs WHERE model_name = :n"
        )
        params: dict[str, Any] = {"n": name}
        if before_version is not None:
            sql += " AND model_version < :v"
            params["v"] = before_version
        sql += " ORDER BY recorded_at ASC, entry_digest ASC"
        async with CloudSqlFactory.get_session_factory()() as session:
            rows = (await session.execute(text(sql), params)).mappings().all()
        return [
            DecisionOnModel(
                entry_digest=r["entry_digest"],
                model_version=int(r["model_version"]),
                model_digest=r["model_digest"],
                verdict=r["verdict"],
                action=r["action"],
                query=CausalQuery.model_validate(_loads(r["query"])),
                unmeasured=tuple(
                    json.loads(r["unmeasured"]) if isinstance(r["unmeasured"], str)
                    else r["unmeasured"]
                ),
                recorded_at=str(r["recorded_at"]),
            )
            for r in rows
        ]
