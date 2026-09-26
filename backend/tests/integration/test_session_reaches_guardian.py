"""
A session run is governed: every path through the platform graph ends in a Guardian
verdict that was written to audit_log, and the response carries that verdict.

docs/AGENTS.md §5.1 draws the session graph with GuardianCheck before Output, and
POST /api/v1/session/run says "The Guardian runs inside every graph execution -- it
cannot be bypassed via this API." Probed through the API, it never ran:

  - a classifiable run (COMPLICATED or COMPLEX) reached PlanAhead with recommendations
    whose confidence the analysis had halved for want of evidence (0.375, 0.35), below
    the planner's 0.4 commit threshold. The planner simulates nothing (register
    planning-simulation-no-data), so "no safe action found" was a threshold on the
    recommendations' own confidence reported as a safety finding, and it routed the
    run to EscalateHuman -> END, around the Guardian;
  - CHAOTIC and DISORDER runs went EscalateHuman -> END by design, leaving no audit
    record of the escalation or of why;
  - /incident re-classified the triage agent's own narrative ("Incident classified as
    COMPLICATED ...", no keyword) instead of the incident, so every non-chaotic
    incident became DISORDER and escalated, and the escalation overwrote the triage
    agent's Guardian verdict with PENDING; CHAOTIC incidents skipped the graph.

Each response below returned recommendations with guardian_verdict PENDING and no
audit_log row for the session.

These tests run on SQLite and, with TEST_DATABASE_URL set, on PostgreSQL.
"""

from __future__ import annotations

import json
import uuid
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import bindparam, column, select, table

from src.api.main import app
from src.db.factory import CloudSqlFactory

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

pytestmark = pytest.mark.asyncio

_USER: dict[str, Any] = {
    "uid": "session-guardian-001",
    "email": "session-guardian@test.com",
    "name": "Session Guardian Test",
    "roles": ["analyst"],
}

_AUDIT_LOG = table(
    "audit_log",
    column("agent_id"),
    column("action_type"),
    column("guardian_verdict"),
    column("policy_rules_cited"),
    column("session_id"),
    column("signature"),
    column("created_at"),
)

#: A latency incident with numbers in it: 'latency', 'deployment' and 'cpu' are the
#: classifier's COMPLICATED keywords, so this run is classified, not DISORDER.
LATENCY_INCIDENT = (
    "p99 latency on the checkout service rose from 180 ms to 4800 ms within ten "
    "minutes of the 14:02 deployment; cpu on the api pods is at 92%."
)
#: 'intermittent', 'multiple' and 'pattern' are COMPLEX keywords.
COMPLEX_INCIDENT = (
    "intermittent latency pattern across multiple services since Tuesday; error "
    "rate between 0.4% and 2.1% with no single change that lines up."
)
CHAOTIC_INCIDENT = "cascading failure of unknown origin, total outage in eu-west"
#: No keyword the stub knows: DISORDER (register cynefin-classifier-keyword-stub).
UNCLASSIFIABLE = "the dashboard looks odd this morning"

_VERDICTS = {"APPROVED", "REJECTED", "REQUIRES_ESCALATION"}


@pytest_asyncio.fixture
async def client(db_engine: object) -> AsyncGenerator[AsyncClient, None]:
    with patch(
        "src.middleware.auth._verify_firebase_token",
        new=AsyncMock(return_value=_USER),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={"Authorization": "Bearer session-guardian-token"},
        ) as ac:
            yield ac


async def _audit_rows(session_id: str) -> list[dict[str, Any]]:
    """Every audit_log row written for a session, oldest first."""
    async with CloudSqlFactory.get_session_factory()() as session:
        result = await session.execute(
            select(_AUDIT_LOG)
            .where(_AUDIT_LOG.c.session_id == bindparam("sid"))
            .order_by(_AUDIT_LOG.c.created_at),
            {"sid": session_id},
        )
        rows = [dict(r) for r in result.mappings()]
    for row in rows:
        rules = row["policy_rules_cited"]
        if isinstance(rules, str):
            row["policy_rules_cited"] = json.loads(rules)
        row["policy_rules_cited"] = list(row["policy_rules_cited"] or [])
    return rows


async def _graph_verdict_row(session_id: str) -> dict[str, Any]:
    """The Guardian row the session graph wrote -- the last one for the session."""
    rows = [r for r in await _audit_rows(session_id) if r["agent_id"] == "guardian-check-node"]
    assert rows, (
        f"no GuardianCheck audit_log row for session {session_id}: the run bypassed the Guardian"
    )
    return rows[-1]


def _assert_governed(data: dict[str, Any], row: dict[str, Any]) -> None:
    """The response's verdict is the Guardian's, as written to audit_log, signed."""
    assert data["guardian_verdict"] in _VERDICTS, data
    assert row["guardian_verdict"] == data["guardian_verdict"]
    assert row["policy_rules_cited"] == data["policy_rules_cited"]
    assert row["signature"], "a GuardianCheck verdict is signed where it is written"


async def _run(client: AsyncClient, description: str) -> dict[str, Any]:
    resp = await client.post("/api/v1/session/run", json={"description": description})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _incident(title: str, severity: str = "P2") -> dict[str, Any]:
    return {
        "incident_id": f"INC-{uuid.uuid4().hex[:6].upper()}",
        "title": title,
        "severity": severity,
        "affected_services": ["checkout", "api-gateway"],
        "metrics_snapshot": {"p99_ms": 4800.0, "cpu_pct": 92.0, "error_rate": 0.021},
    }


# ── Classified runs reach the Guardian ────────────────────────────────────────


async def test_complicated_run_reaches_guardian(client: AsyncClient) -> None:
    data = await _run(client, LATENCY_INCIDENT)

    assert data["cynefin_domain"] == "COMPLICATED"
    assert data["recommendation_count"] >= 1
    _assert_governed(data, await _graph_verdict_row(data["session_id"]))
    assert "no_safe_action_found" not in data["flags"], (
        "the planner simulated nothing, so it has no finding that the action is unsafe"
    )


async def test_complex_run_reaches_guardian(client: AsyncClient) -> None:
    data = await _run(client, COMPLEX_INCIDENT)

    assert data["cynefin_domain"] == "COMPLEX"
    _assert_governed(data, await _graph_verdict_row(data["session_id"]))
    # COMPLEX still goes to a person after the Guardian (expert elicitation).
    assert data["requires_human_review"] is True


async def test_unsimulated_plan_is_marked_and_still_goes_to_a_person(client: AsyncClient) -> None:
    """Nothing was estimated, so the plan says so and no action is presented as vetted."""
    data = await _run(client, LATENCY_INCIDENT)

    assert "plan_unsimulated" in data["flags"]
    assert data["requires_human_review"] is True
    for rec in data["recommendations"]:
        assert "not simulated" in rec["rationale"].lower(), rec


async def test_incident_classifies_the_incident_not_the_triage_narrative(
    client: AsyncClient,
) -> None:
    resp = await client.post(
        "/api/v1/session/incident",
        json=_incident("p99 latency on checkout after the 14:02 deployment"),
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert data["cynefin_domain"] == "COMPLICATED", data["flags"]
    _assert_governed(data, await _graph_verdict_row(data["session_id"]))


# ── Escalations are recorded, with why ────────────────────────────────────────


async def test_chaotic_run_escalates_with_an_audit_record(client: AsyncClient) -> None:
    data = await _run(client, CHAOTIC_INCIDENT)

    assert data["cynefin_domain"] == "CHAOTIC"
    assert data["requires_human_review"] is True
    assert data["proposed_edge_count"] == 0
    row = await _graph_verdict_row(data["session_id"])
    _assert_governed(data, row)
    assert data["guardian_verdict"] == "REQUIRES_ESCALATION"
    assert row["action_type"] == "GRAPH_HUMAN_ESCALATION"
    assert "PR-003" in row["policy_rules_cited"]
    assert data["recommendations"][0]["action"].startswith("ESCALATE")


async def test_disorder_run_escalates_with_an_audit_record(client: AsyncClient) -> None:
    data = await _run(client, UNCLASSIFIABLE)

    assert data["cynefin_domain"] == "DISORDER"
    assert data["requires_human_review"] is True
    row = await _graph_verdict_row(data["session_id"])
    _assert_governed(data, row)
    assert row["action_type"] == "GRAPH_HUMAN_ESCALATION"
    assert "PR-006" in row["policy_rules_cited"]
    assert "CHAOTIC" not in data["draft_explanation"], (
        "a DISORDER escalation must say why it escalated, not that the domain was CHAOTIC"
    )


async def test_chaotic_incident_escalates_with_an_audit_record(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/v1/session/incident",
        json=_incident("service cluster failure unknown root cause", severity="P0"),
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert data["cynefin_domain"] == "CHAOTIC"
    row = await _graph_verdict_row(data["session_id"])
    _assert_governed(data, row)
    assert "PR-003" in row["policy_rules_cited"]
    escalations = [r for r in data["recommendations"] if r["action"].startswith("ESCALATE")]
    assert len(escalations) == 1, data["recommendations"]


async def test_planner_escalation_is_ruled_on_and_recorded(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A lookahead that estimated every candidate unsafe escalates -- through the Guardian.

    Unreachable through the API today (the planner is given no data and estimates
    nothing: register planning-simulation-no-data), so the estimate is supplied here.
    """
    from src.orchestration import planning

    async def _estimated_harm(**_: object) -> tuple[float | None, float, bool, str | None]:
        return (-0.4, 0.1, True, "estimated harm")

    monkeypatch.setattr(planning, "_simulate_single_action", _estimated_harm)
    data = await _run(client, LATENCY_INCIDENT)

    assert "escalation:planning_no_safe_action" in data["flags"]
    assert data["requires_human_review"] is True
    assert data["recommendations"][0]["action"].startswith("ESCALATE")
    row = await _graph_verdict_row(data["session_id"])
    _assert_governed(data, row)
    assert row["action_type"] == "GRAPH_HUMAN_ESCALATION"


# ── The invariant ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "description",
    [LATENCY_INCIDENT, COMPLEX_INCIDENT, CHAOTIC_INCIDENT, UNCLASSIFIABLE, "config timeout"],
    ids=["complicated", "complex", "chaotic", "disorder", "terse"],
)
async def test_no_run_returns_a_recommendation_without_a_verdict(
    client: AsyncClient, description: str
) -> None:
    data = await _run(client, description)

    if data["recommendations"]:
        _assert_governed(data, await _graph_verdict_row(data["session_id"]))


async def test_resumed_run_is_governed_again(client: AsyncClient) -> None:
    first = await _run(client, LATENCY_INCIDENT)
    resp = await client.post(
        "/api/v1/session/run",
        json={"description": LATENCY_INCIDENT, "session_id": first["session_id"]},
    )
    assert resp.status_code == 200, resp.text
    second = resp.json()

    rows = [
        r for r in await _audit_rows(first["session_id"]) if r["agent_id"] == "guardian-check-node"
    ]
    assert len(rows) == 2, "each run of the graph writes its own Guardian verdict"
    _assert_governed(second, rows[-1])
