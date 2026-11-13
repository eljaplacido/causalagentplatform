"""
What a session or incident run leaves in the audit trail, and what it stores.

Three defects from the session-Guardian review and the 2026-09-26 audit:

  septa-79  A session run that failed before GuardianCheck (an exception, an open
            circuit breaker) answered with a verdict the API made up -- REJECTED or
            REQUIRES_ESCALATION -- and wrote nothing: the trail had no sign of the
            run or of why it ended as it did.
  septa-80  POST /api/v1/session/incident analysed an incident twice -- the triage
            agent, with a Guardian check of its own, then the graph's CausalAnalysis
            over the triage's output, with another -- so one incident had two
            analyses and two Guardian rows for the same run.
  septa-85  Every stored intervention carried the whole session's verdict, which
            the Guardian had never given that intervention.

Preconditions: Firebase verification patched; the SQLite test engine, and with
TEST_DATABASE_URL set the PostgreSQL one built from the migrations.

Postconditions
  1. A run that fails before GuardianCheck leaves one audit_log row for its
     session: unsigned (history, never authority), with the verdict the API
     answered with and notes saying why.
  2. An incident run is analysed once (one IncidentTriage step, no
     CausalDiscovery step) and ruled on once: one run-level Guardian row, no
     triage-agent Guardian row.
  3. Each stored intervention carries the verdict of a Guardian check of that
     intervention, written to its own audit_log row (guardianVerdictRef); two
     interventions the Guardian rules on differently are stored differently.

Undefined here: the wording of the notes beyond naming the cause, and which
verdict an unpatched Guardian gives a COMPLICATED incident's interventions.
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

from src.agents.governance import GovernanceAgent
from src.api import session as session_api
from src.api.main import app
from src.db.factory import CloudSqlFactory
from src.exceptions import CircuitBreakerError
from src.semantic_layer.models import GuardianVerdict

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

pytestmark = pytest.mark.asyncio

_USER: dict[str, Any] = {
    "uid": "incident-integrity-001",
    "email": "incident-integrity@test.com",
    "name": "Incident Integrity Test",
    "roles": ["sre"],
}

_AUDIT_LOG = table(
    "audit_log",
    column("id"),
    column("agent_id"),
    column("action_type"),
    column("guardian_verdict"),
    column("policy_rules_cited"),
    column("session_id"),
    column("signature"),
    column("notes"),
    column("created_at"),
)

#: 'deployment', 'cpu' and 'latency' are the classifier's COMPLICATED keywords.
COMPLICATED_TITLE = "p99 latency on checkout after the 14:02 deployment, cpu at 92%"


@pytest_asyncio.fixture
async def client(db_engine: object) -> AsyncGenerator[AsyncClient, None]:
    with patch(
        "src.middleware.auth._verify_firebase_token",
        new=AsyncMock(return_value=_USER),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={"Authorization": "Bearer incident-integrity-token"},
        ) as ac:
            yield ac


async def _audit_rows(session_id: str) -> list[dict[str, Any]]:
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
            rules = json.loads(rules)
        row["policy_rules_cited"] = list(rules or [])
        row["id"] = str(row["id"])
    return rows


def _incident(title: str, severity: str = "P2") -> dict[str, Any]:
    return {
        "incident_id": f"INC-{uuid.uuid4().hex[:6].upper()}",
        "title": title,
        "severity": severity,
        "affected_services": ["checkout", "api-gateway"],
    }


class _FailingGraph:
    """Stands in for the compiled graph: raises before any node runs."""

    def __init__(self, exc: BaseException) -> None:
        self._exc = exc

    async def ainvoke(self, _state: object) -> object:
        raise self._exc


# ── septa-79: a fallback verdict is recorded, unsigned, with why ──────────────


@pytest.mark.parametrize(
    ("exc", "action_type", "verdict", "cause"),
    [
        (RuntimeError("forced graph failure"), "GRAPH_INTERNAL_ERROR", "REJECTED",
         "forced graph failure"),
        (CircuitBreakerError(cycle_count=5, session_id="x"), "GRAPH_CIRCUIT_BREAKER",
         "REQUIRES_ESCALATION", "circuit breaker"),
    ],
    ids=["internal-error", "circuit-breaker"],
)
async def test_failed_run_leaves_an_unsigned_row_saying_why(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    exc: BaseException,
    action_type: str,
    verdict: str,
    cause: str,
) -> None:
    monkeypatch.setattr(session_api, "_graph", _FailingGraph(exc))

    resp = await client.post("/api/v1/session/run", json={"description": "config timeout"})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["guardian_verdict"] == verdict
    assert data["recommendations"] == []

    rows = await _audit_rows(data["session_id"])
    assert len(rows) == 1, rows
    row = rows[0]
    assert row["action_type"] == action_type
    assert row["guardian_verdict"] == verdict
    assert not row["signature"], "the API's verdict is history, never spendable authority"
    assert row["policy_rules_cited"] == [], "no policy ran"
    assert cause in row["notes"].lower(), row["notes"]
    assert "not ruled by the guardian" in row["notes"].lower(), row["notes"]


# ── septa-80: one analysis, one run-level Guardian row per incident ───────────


async def test_incident_is_analysed_once_and_ruled_on_once(client: AsyncClient) -> None:
    resp = await client.post("/api/v1/session/incident", json=_incident(COMPLICATED_TITLE))
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["cynefin_domain"] == "COMPLICATED", data["flags"]
    assert data["recommendation_count"] >= 1

    final = session_api._session_cache[data["session_id"]]
    analyses = [
        (s.node, s.action) for s in final.reasoning_steps
        if s.node in ("IncidentTriage", "CausalDiscovery")
    ]
    # The triage's own CYNEFIN_CLASSIFY step belonged to process(); the graph
    # classifies. One TRIAGE_COMPLETE, and CausalDiscovery never ran.
    assert analyses == [("IncidentTriage", "TRIAGE_COMPLETE")], analyses

    rows = await _audit_rows(data["session_id"])
    run_rows = [r for r in rows if r["agent_id"] != "guardian-intervention-check"]
    assert [r["agent_id"] for r in run_rows] == ["guardian-check-node"], run_rows
    assert run_rows[0]["guardian_verdict"] == data["guardian_verdict"]
    # Besides the run's verdict, exactly one check per stored intervention.
    assert len(rows) == 1 + len(data["interventions"])


# ── septa-85: each intervention gets a verdict of its own ─────────────────────


async def test_each_intervention_carries_its_own_verdict(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two interventions of one session, ruled on differently, are stored differently.

    No rule today tells one intervention of a session from another (each rule
    reads the session's context or its edge set), so the Guardian's ruling on
    intervention rank 2 is set here: REJECTED, everything else as the policies
    say. Known truth by construction -- what is under test is that each stored
    intervention carries the ruling on itself, not the session's.
    """
    evaluate = GovernanceAgent._evaluate_policies

    def _rank_two_rejected(
        self: GovernanceAgent, payload: dict[str, Any], *args: Any, **kwargs: Any
    ) -> tuple[GuardianVerdict, str, list[str]]:
        intervention = payload.get("intervention") or {}
        if intervention.get("rank") == 2:
            return GuardianVerdict.REJECTED, "set by the test", ["TEST-RANK-2"]
        return evaluate(self, payload, *args, **kwargs)

    monkeypatch.setattr(GovernanceAgent, "_evaluate_policies", _rank_two_rejected)

    # Two interventions: a committed plan keeps one action, so the lookahead is
    # given an estimate that no action survives (as test_session_reaches_guardian
    # does). The run escalates with the directive first and the triage's
    # intervention second; PR-010 rules on both unless the test says otherwise.
    from src.orchestration import planning

    async def _estimated_harm(**_: object) -> tuple[float | None, float, bool, str | None]:
        return (-0.4, 0.1, True, "estimated harm")

    monkeypatch.setattr(planning, "_simulate_single_action", _estimated_harm)

    body = _incident(COMPLICATED_TITLE)
    resp = await client.post("/api/v1/session/incident", json=body)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["stored"] is True, data.get("store_error")
    interventions = data["interventions"]
    assert len(interventions) >= 2, interventions

    by_rank = {i["rank"]: i for i in interventions}
    assert by_rank[2]["guardianVerdict"] == "REJECTED"
    assert by_rank[1]["guardianVerdict"] == data["guardian_verdict"] == "REQUIRES_ESCALATION", (
        "rank 1 is ruled by the policies (PR-010), as the run was"
    )
    assert "PR-010" in by_rank[1]["policyRulesCited"]

    rows = {r["id"]: r for r in await _audit_rows(data["session_id"])}
    for intervention in interventions:
        row = rows[intervention["guardianVerdictRef"]]
        assert row["agent_id"] == "guardian-intervention-check"
        assert row["guardian_verdict"] == intervention["guardianVerdict"]
        assert row["policy_rules_cited"] == intervention["policyRulesCited"]
        assert row["signature"], "a Guardian check is signed where it is written"
    assert len({i["guardianVerdictRef"] for i in interventions}) == len(interventions)

    stored = await client.get(f"/api/v1/incidents/{body['incident_id']}")
    assert stored.status_code == 200, stored.text
    stored_verdicts = {
        i["id"]: i["guardianVerdict"] for i in stored.json()["proposed_interventions"]
    }
    assert stored_verdicts == {i["id"]: i["guardianVerdict"] for i in interventions}


async def test_chaotic_incident_interventions_are_each_ruled_on(client: AsyncClient) -> None:
    """Unpatched: each intervention has its own check, and PR-003 binds each one."""
    resp = await client.post(
        "/api/v1/session/incident",
        json=_incident("service cluster failure unknown root cause", severity="P0"),
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["cynefin_domain"] == "CHAOTIC"
    assert data["interventions"], data
    rows = {r["id"]: r for r in await _audit_rows(data["session_id"])}
    for intervention in data["interventions"]:
        row = rows[intervention["guardianVerdictRef"]]
        assert row["action_type"] == "INCIDENT_INTERVENTION_CHECK"
        assert intervention["guardianVerdict"] == "REQUIRES_ESCALATION"
        assert "PR-003" in intervention["policyRulesCited"]
