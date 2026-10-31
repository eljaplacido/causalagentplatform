"""
Every drift alert leaves a record, with or without Neo4j (septa-62).

DriftMonitorAgent stored its alerts only as (:DriftAlert) nodes in Neo4j, and
only when NEO4J_URI was set. Neo4j is an optional side store (docs/DATA_LAYER.md
§3.1); on a deployment without it an alert was logged by the in-process
AlertEmitter and kept nowhere. The agent now writes every alert that Neo4j did
not store to the SQL ``drift_alerts`` table (015_drift_alerts.sql, and its
SQLITE_DDL rendering), through src/sensors/drift_alert_log.py.

Preconditions: a database built by the app's schema (SQLite, or PostgreSQL from
the migrations when TEST_DATABASE_URL is set, where the app connects as
causal_app); a DriftMonitorAgent.

Postconditions:
  * without a Neo4j driver, an alert the agent raises is a drift_alerts row
    carrying the alert as raised: its type, severity, drift type, the measured
    score, the drifted features, the fragment and the session; the agent's
    result says it was stored, and in SQL;
  * the same holds when Neo4j storage is disabled, and when the Neo4j write fails;
  * an alert Neo4j did store is not written to SQL a second time;
  * get_recent_alerts, with no Neo4j, returns the SQL records, newest first;
  * through process(), a severe distribution drift (KL 0.8, a value the test
    sets) leaves a CRITICAL row whose score is 0.8.

Undefined here: the row id's format, the wording of description and
recommendation, and the content of `context` beyond what is named above.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from sqlalchemy import text

from src.agents.drift_monitor import DriftMonitorAgent
from src.db.types import json_value
from src.semantic_layer.models import CynefinDomain, EpistemicState

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio

_NO_NEO4J = "src.skills.drift_detect.Neo4jFactory.get_driver"


def _state(fragment_id: str | None = None) -> EpistemicState:
    return EpistemicState(
        session_id=f"drift-record-{uuid4().hex[:8]}",
        agent_id="drift-record-test",
        cynefin_domain=CynefinDomain.COMPLICATED,
        active_graph_fragment_id=fragment_id or str(uuid4()),
    )


async def _raise(agent: DriftMonitorAgent, state: EpistemicState, **over: Any) -> dict[str, Any]:
    alert: dict[str, Any] = {
        "alert_type": "CAUSAL_STRUCTURE_DRIFT",
        "severity": "HIGH",
        "drift_score": 0.37,
        "drifted_features": ["latency", "error_rate"],
        "drift_type": "causal",
        "description": "edge latency -> error_rate no longer holds",
        "recommendation": "re-review the fragment",
    }
    alert.update(over)
    return await agent._emit_and_store_alert(state=state, **alert)


async def _rows(db_session: AsyncSession) -> list[dict[str, Any]]:
    result = await db_session.execute(
        text(
            "SELECT alert_type, severity, drift_type, fragment_id, drift_score, "
            "drifted_features, session_id, agent_id, created_at FROM drift_alerts"
        )
    )
    return [dict(r) for r in result.mappings()]


async def test_without_neo4j_the_alert_is_a_sql_row(db_session: AsyncSession) -> None:
    agent = DriftMonitorAgent(agent_id="drift-agent-a")
    state = _state()
    with patch(_NO_NEO4J, return_value=None):
        result = await _raise(agent, state)

    assert result["stored"] is True
    assert result["stored_in"] == "sql"
    rows = await _rows(db_session)
    assert len(rows) == 1
    row = rows[0]
    assert row["alert_type"] == "CAUSAL_STRUCTURE_DRIFT"
    assert row["severity"] == "HIGH"
    assert row["drift_type"] == "causal"
    assert row["drift_score"] == pytest.approx(0.37)
    assert json_value(row["drifted_features"]) == ["latency", "error_rate"]
    assert row["fragment_id"] == state.active_graph_fragment_id
    assert row["session_id"] == state.session_id
    assert row["agent_id"] == "drift-agent-a"
    assert row["created_at"] is not None


async def test_with_neo4j_storage_disabled_the_alert_is_still_recorded(
    db_session: AsyncSession,
) -> None:
    agent = DriftMonitorAgent(enable_neo4j_storage=False)
    result = await _raise(agent, _state(), severity="MEDIUM", alert_type="SEMANTIC_DRIFT")
    assert result["stored"] is True
    assert [(r["alert_type"], r["severity"]) for r in await _rows(db_session)] == [
        ("SEMANTIC_DRIFT", "MEDIUM")
    ]


async def test_a_failed_neo4j_write_falls_back_to_sql(db_session: AsyncSession) -> None:
    agent = DriftMonitorAgent()
    with patch(
        "src.agents.drift_monitor.store_drift_alert",
        new=AsyncMock(side_effect=RuntimeError("bolt connection refused")),
    ):
        result = await _raise(agent, _state())
    assert result["stored"] is True
    assert result["stored_in"] == "sql"
    assert "bolt connection refused" in result["storage_error"]
    assert len(await _rows(db_session)) == 1


async def test_an_alert_neo4j_stored_is_not_written_twice(db_session: AsyncSession) -> None:
    agent = DriftMonitorAgent()
    with patch(
        "src.agents.drift_monitor.store_drift_alert",
        new=AsyncMock(return_value={"stored": True, "alert_id": "neo-1"}),
    ):
        result = await _raise(agent, _state())
    assert result["stored"] is True
    assert result["stored_in"] == "neo4j"
    assert await _rows(db_session) == []


async def test_recent_alerts_without_neo4j_come_from_sql(db_session: AsyncSession) -> None:
    agent = DriftMonitorAgent(enable_alert_emission=False)
    fragment = str(uuid4())
    with patch(_NO_NEO4J, return_value=None):
        await _raise(agent, _state(fragment), alert_type="FIRST", drift_score=0.1)
        await _raise(agent, _state(fragment), alert_type="SECOND", drift_score=0.2)
        await _raise(agent, _state(), alert_type="ELSEWHERE", drift_score=0.3)
        recent = await agent.get_recent_alerts(fragment_id=fragment)

    assert recent["retrieval_source"] == "sql_drift_alerts"
    assert recent["count"] == 2
    assert [a["alert_type"] for a in recent["alerts"]] == ["SECOND", "FIRST"]
    assert [a["drift_score"] for a in recent["alerts"]] == pytest.approx([0.2, 0.1])


async def test_a_severe_drift_found_by_process_leaves_a_critical_row(
    db_session: AsyncSession,
) -> None:
    agent = DriftMonitorAgent()

    async def severe(_inputs: dict[str, Any]) -> dict[str, Any]:
        return {
            "kl_divergence": 0.8,
            "drifted_features": ["latency"],
            "drift_type": "distribution",
            "feature_scores": {"latency": 0.8},
        }

    async def no_structure(_inputs: dict[str, Any]) -> dict[str, Any]:
        return {"structural_drift_detected": False, "affected_edges": []}

    async def no_semantic(_inputs: dict[str, Any]) -> dict[str, Any]:
        return {"semantic_drift": False, "drifted_entities": []}

    agent._skill_drift_detect = severe  # type: ignore[method-assign]
    agent._skill_causal_structure_compare = no_structure  # type: ignore[method-assign]
    agent._skill_semantic_drift_detect = no_semantic  # type: ignore[method-assign]

    state = _state()
    with patch(_NO_NEO4J, return_value=None):
        await agent.process(state)

    rows = await _rows(db_session)
    critical = [r for r in rows if r["severity"] == "CRITICAL"]
    assert len(critical) == 1, rows
    assert critical[0]["drift_score"] == pytest.approx(0.8)
    assert critical[0]["drift_type"] == "distribution"
    assert critical[0]["fragment_id"] == state.active_graph_fragment_id
