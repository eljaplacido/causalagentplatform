"""
Guardian PR-007: an edge without a sensitivity result is not approved.

This is the test the register's `sensitivity-gate-not-enforced` entry named (septa-33).
PR-007 sat in the Guardian's rule table -- "edges without sensitivity_result blocked
from APPROVED" -- and nowhere in its evaluation, so a check to approve an edge was
APPROVED whether or not any sensitivity analysis had been run, and PR-007 was never
cited on a verdict.

What counts as an edge's sensitivity result. `CausalEdge` has no sensitivity field;
the platform stores a `SensitivityResult` (E-value / Rosenbaum, 001_initial.sql
`assumptions.sensitivity_result`) on the *assumptions* an edge rests on, which the edge
names in `assumption_refs`. An edge has a sensitivity result when at least one
assumption it references carries a valid one that **the platform recorded**, and has
not been REJECTED in review. The Guardian reads that from the database, never from the
payload being checked: a caller who could attach its own result would choose it.

**Which results count (provenance).** A drafter can put a SensitivityResult on an
assumption they draft -- its `computed_by` is free text -- so a stored result alone
proves nothing: counting it let a drafter satisfy PR-007 with a made-up number. A
result counts only when it was recorded by SemanticLayerRegistry.record_edge_sensitivity
(the kernel's analysis, POST /api/semantic/edges/{id}/sensitivity or SimulationAgent),
which stores the id of the verdict it spent in `assumptions.sensitivity_verdict_id`
(013). The reading verifies that id: an audit_log row that is APPROVED, issued for
RECORD_SENSITIVITY, signed by the Guardian, whose subject is *the edge being judged*,
and spent (verdict_consumption) by record_edge_sensitivity. A verdict records one
result (the column is unique). propose_draft never writes the column.

Preconditions: a check for action GOVERNANCE_APPROVE (the decision PR-007 governs)
whose subject is an edge stored in `causal_edges`, in a classified situation
(COMPLICATED), so that no earlier rule decides first.

Postconditions:
  * the edge has no sensitivity result -> the verdict is REJECTED and cites PR-007;
  * the edge has one the platform recorded -> PR-007 does not refuse it: APPROVED,
    PR-007 not cited, and the verdict approves the edge through /approve;
  * a result a drafter attached to an assumption does not count, whatever it says;
  * a forged verdict reference does not count: unsigned, issued for another action or
    another edge, REJECTED, or never spent;
  * a sensitivity result in the checked payload changes nothing;
  * a subject that is not an edge is not judged by PR-007.

Undefined here: whether a result that says "not robust" should also block (PR-007 asks
for a result, not a verdict on it -- the reviewer sees it), and checks for other actions
(proposing a draft is not approving it).

The sensitivity results are the kernel's own: `causal_core.analyze` on data simulated
with a known effect, its E-value carried into a `SensitivityResult` the way
SimulationAgent reads it (robust when the E-value's confidence bound is at least 2).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import numpy as np
import pytest
import pytest_asyncio
from causal_core import analyze
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from src.semantic_layer.models import (
    Assumption,
    AssumptionType,
    CausalEdge,
    CausalObjectRef,
    CynefinDomain,
    EdgeType,
    ElicitedFrom,
    EpistemicState,
    SensitivityResult,
)
from src.semantic_layer.verdict import RECORD_SENSITIVITY
from tests.conftest import (
    VerdictFactory,
    insert_raw_audit_entry,
    issue_verdict_for,
    make_causal_entity,
    make_user_ref,
)

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from src.agents.governance import GovernanceAgent
    from src.semantic_layer.registry import SemanticLayerRegistry

pytestmark = [pytest.mark.asyncio, pytest.mark.guardian]

GOVERNANCE_APPROVE = "GOVERNANCE_APPROVE"
GRAPH = "load -> playbook; load -> minutes; playbook -> minutes"


@pytest.fixture(scope="module")
def kernel_sensitivity() -> SensitivityResult:
    """The kernel's sensitivity analysis of a playbook whose true effect is -3 minutes."""
    rng = np.random.default_rng(11)
    n = 2000
    load = rng.normal(size=n)
    playbook = (rng.random(n) < 1 / (1 + np.exp(-load))).astype(float)
    minutes = 10 + 2 * load - 3.0 * playbook + rng.normal(size=n)
    out = analyze(
        {"load": load, "playbook": playbook, "minutes": minutes},
        GRAPH,
        treatment="playbook",
        outcome="minutes",
    ).model_dump(mode="json")
    assert out["kind"] == "result" and out["status"] == "supported", out
    sens = out["sensitivity"]
    assert sens["e_value"] is not None and sens["e_value_ci"] is not None
    return SensitivityResult(
        e_value=sens["e_value"],
        robust_to_unmeasured_confounding=sens["e_value_ci"] >= 2.0,
        analysis_method=sens["e_value_method"],
        computed_at=datetime.now(UTC),
        computed_by="causal-core",
    )


def _state() -> EpistemicState:
    return EpistemicState(
        session_id="pr007-test",
        agent_id="pr007-test",
        cynefin_domain=CynefinDomain.COMPLICATED,
    )


def _assumption(sensitivity: SensitivityResult | None) -> Assumption:
    return Assumption(
        statement="No unmeasured cause of both the playbook and the minutes",
        assumption_type=AssumptionType.IGNORABILITY,
        justification="Load is the only known common cause and it is measured",
        elicited_from=ElicitedFrom.AGENT,
        elicited_by=make_user_ref(),
        domain="devops",
        sensitivity_result=sensitivity,
    )


async def _edge(
    registry: SemanticLayerRegistry,
    verdict_for: VerdictFactory,
    *assumptions: Assumption,
) -> CausalEdge:
    """Store the assumptions and a DRAFT edge resting on them, through the registry."""
    for assumption in assumptions:
        await registry.propose_draft(assumption, await verdict_for(assumption.id))
    edge_id = uuid4()
    edge = CausalEdge(
        id=edge_id,
        source=CausalObjectRef(id=uuid4(), object_type="CausalEntity"),
        target=CausalObjectRef(id=uuid4(), object_type="Outcome"),
        edge_type=EdgeType.CAUSES,
        confidence=0.8,
        assumption_refs=[a.id for a in assumptions],
        guardian_verdict_ref=uuid4(),
        domain="devops",
    )
    await registry.propose_draft(edge, await verdict_for(edge_id))
    return edge


async def _record(
    registry: SemanticLayerRegistry,
    db_session: AsyncSession,
    edge_id: UUID,
    result: SensitivityResult,
) -> tuple[UUID, UUID]:
    """Record ``result`` on the edge the platform's way; return (assumption, verdict)."""
    verdict = await issue_verdict_for(db_session, edge_id, action_type=RECORD_SENSITIVITY)
    assumption = await registry.record_edge_sensitivity(
        edge_id, result, verdict, make_user_ref("platform")
    )
    return assumption, verdict


def _approve_payload(subject: UUID, **extra: Any) -> dict[str, Any]:
    return {
        "action_type": GOVERNANCE_APPROVE,
        "object_id": str(subject),
        "object_type": "CausalEdge",
        "subject_id": str(subject),
        **extra,
    }


# ── The rule, on the agent ───────────────────────────────────────────────────


async def test_an_edge_with_no_assumptions_is_refused_on_pr007(
    db_engine: object,
    governance: GovernanceAgent,
    registry: SemanticLayerRegistry,
    verdict_for: VerdictFactory,
) -> None:
    edge = await _edge(registry, verdict_for)
    result = await governance.check(_approve_payload(edge.id), _state())
    assert result.verdict == "REJECTED", result.reason
    assert result.policy_rules_cited == ["PR-007"]
    assert "sensitivity" in result.reason


async def test_an_edge_whose_assumptions_carry_no_result_is_refused_on_pr007(
    db_engine: object,
    governance: GovernanceAgent,
    registry: SemanticLayerRegistry,
    verdict_for: VerdictFactory,
) -> None:
    edge = await _edge(registry, verdict_for, _assumption(None), _assumption(None))
    result = await governance.check(_approve_payload(edge.id), _state())
    assert result.verdict == "REJECTED", result.reason
    assert result.policy_rules_cited == ["PR-007"]


async def test_a_platform_recorded_result_satisfies_pr007(
    db_engine: object,
    governance: GovernanceAgent,
    registry: SemanticLayerRegistry,
    verdict_for: VerdictFactory,
    db_session: AsyncSession,
    kernel_sensitivity: SensitivityResult,
) -> None:
    edge = await _edge(registry, verdict_for, _assumption(None))
    await _record(registry, db_session, edge.id, kernel_sensitivity)
    result = await governance.check(_approve_payload(edge.id), _state())
    assert result.verdict == "APPROVED", result.reason
    assert "PR-007" not in result.policy_rules_cited


async def test_a_drafter_attached_result_does_not_satisfy_pr007(
    db_engine: object,
    governance: GovernanceAgent,
    registry: SemanticLayerRegistry,
    verdict_for: VerdictFactory,
    kernel_sensitivity: SensitivityResult,
) -> None:
    """The drafter's own number, on the drafter's own assumption, is not evidence."""
    made_up = kernel_sensitivity.model_copy(
        update={"e_value": 9.9, "robust_to_unmeasured_confounding": True,
                "computed_by": "causal-core"}
    )
    edge = await _edge(registry, verdict_for, _assumption(None), _assumption(made_up))
    result = await governance.check(_approve_payload(edge.id), _state())
    assert result.verdict == "REJECTED", result.reason
    assert result.policy_rules_cited == ["PR-007"]
    assert "recorded" in result.reason


async def test_record_edge_sensitivity_stores_the_verdict_it_spent(
    db_engine: object,
    registry: SemanticLayerRegistry,
    verdict_for: VerdictFactory,
    db_session: AsyncSession,
    kernel_sensitivity: SensitivityResult,
) -> None:
    edge = await _edge(registry, verdict_for)
    assumption, verdict = await _record(registry, db_session, edge.id, kernel_sensitivity)
    row = await db_session.execute(
        text("SELECT sensitivity_verdict_id FROM assumptions WHERE id = :i"),
        {"i": str(assumption)},
    )
    assert str(row.scalar()) == str(verdict)


async def test_propose_draft_never_writes_a_verdict_reference(
    db_engine: object,
    registry: SemanticLayerRegistry,
    verdict_for: VerdictFactory,
    db_session: AsyncSession,
    kernel_sensitivity: SensitivityResult,
) -> None:
    """A drafter who sends a real, spent RECORD_SENSITIVITY verdict id gets nothing."""
    edge = await _edge(registry, verdict_for)
    _, spent = await _record(registry, db_session, edge.id, kernel_sensitivity)
    body = _assumption(kernel_sensitivity).model_dump(mode="json")
    body["sensitivity_verdict_id"] = str(spent)
    body["sensitivityVerdictId"] = str(spent)
    drafted = Assumption.model_validate(body)
    await registry.propose_draft(drafted, await verdict_for(drafted.id))
    row = await db_session.execute(
        text("SELECT sensitivity_verdict_id FROM assumptions WHERE id = :i"),
        {"i": str(drafted.id)},
    )
    assert row.scalar() is None


_FORGERIES = ["unsigned", "other_action", "other_edge", "rejected", "never_spent"]


@pytest.mark.parametrize("forgery", _FORGERIES)
async def test_a_forged_verdict_reference_does_not_satisfy_pr007(
    db_engine: object,
    governance: GovernanceAgent,
    registry: SemanticLayerRegistry,
    verdict_for: VerdictFactory,
    db_session: AsyncSession,
    kernel_sensitivity: SensitivityResult,
    forgery: str,
) -> None:
    """A drafter's result pointed at a verdict the platform did not record it under.

    Written into the column directly: the API offers no way to set it, so this is the
    database-level attempt, and the reading must still refuse it.
    """
    drafted = _assumption(kernel_sensitivity)
    edge = await _edge(registry, verdict_for, drafted)
    if forgery == "unsigned":
        ref = await insert_raw_audit_entry(
            db_session, subject_id=edge.id, action_type=RECORD_SENSITIVITY
        )
    elif forgery == "other_action":
        ref = await issue_verdict_for(db_session, edge.id, action_type=GOVERNANCE_APPROVE)
    elif forgery == "other_edge":
        # Signed and spent the way record_edge_sensitivity spends it -- for another edge.
        # (The platform's own row for that edge cannot be reused: the column is unique.)
        other = await _edge(registry, verdict_for)
        ref = await issue_verdict_for(db_session, other.id, action_type=RECORD_SENSITIVITY)
        await db_session.execute(
            text(
                "INSERT INTO verdict_consumption (verdict_id, subject_id, context, consumed_at) "
                "VALUES (:v, :s, 'record_edge_sensitivity(CausalEdge)', :t)"
            ),
            {"v": str(ref), "s": str(other.id), "t": datetime.now(UTC)},
        )
    elif forgery == "rejected":
        ref = await issue_verdict_for(
            db_session, edge.id, verdict="REJECTED", action_type=RECORD_SENSITIVITY
        )
    else:  # a real APPROVED RECORD_SENSITIVITY verdict for this edge, never spent
        ref = await issue_verdict_for(db_session, edge.id, action_type=RECORD_SENSITIVITY)
    await db_session.execute(
        text("UPDATE assumptions SET sensitivity_verdict_id = :v WHERE id = :i"),
        {"v": str(ref), "i": str(drafted.id)},
    )
    await db_session.commit()

    result = await governance.check(_approve_payload(edge.id), _state())
    assert result.verdict == "REJECTED", (forgery, result.reason)
    assert result.policy_rules_cited == ["PR-007"]


async def test_a_verdict_records_one_result(
    db_engine: object,
    registry: SemanticLayerRegistry,
    verdict_for: VerdictFactory,
    db_session: AsyncSession,
    kernel_sensitivity: SensitivityResult,
) -> None:
    """The platform's spent verdict cannot be copied onto a second (drafted) assumption."""
    drafted = _assumption(kernel_sensitivity)
    edge = await _edge(registry, verdict_for, drafted)
    _, spent = await _record(registry, db_session, edge.id, kernel_sensitivity)
    with pytest.raises(IntegrityError):
        await db_session.execute(
            text("UPDATE assumptions SET sensitivity_verdict_id = :v WHERE id = :i"),
            {"v": str(spent), "i": str(drafted.id)},
        )
        await db_session.commit()
    await db_session.rollback()


async def test_a_sensitivity_result_in_the_payload_does_not_count(
    db_engine: object,
    governance: GovernanceAgent,
    registry: SemanticLayerRegistry,
    verdict_for: VerdictFactory,
    kernel_sensitivity: SensitivityResult,
) -> None:
    """The Guardian reads the stored edge; what the caller attaches is not evidence."""
    edge = await _edge(registry, verdict_for)
    payload = _approve_payload(
        edge.id, sensitivity_result=kernel_sensitivity.model_dump(mode="json")
    )
    result = await governance.check(payload, _state())
    assert result.verdict == "REJECTED", result.reason
    assert result.policy_rules_cited == ["PR-007"]


async def test_a_result_on_a_rejected_assumption_does_not_count(
    db_engine: object,
    governance: GovernanceAgent,
    registry: SemanticLayerRegistry,
    verdict_for: VerdictFactory,
    db_session: AsyncSession,
    kernel_sensitivity: SensitivityResult,
) -> None:
    edge = await _edge(registry, verdict_for)
    rejected, _ = await _record(registry, db_session, edge.id, kernel_sensitivity)
    await db_session.execute(
        text("UPDATE assumptions SET review_status = 'REJECTED' WHERE id = :i"),
        {"i": str(rejected)},
    )
    await db_session.commit()

    result = await governance.check(_approve_payload(edge.id), _state())
    assert result.verdict == "REJECTED", result.reason
    assert result.policy_rules_cited == ["PR-007"]


async def test_a_subject_that_is_not_an_edge_is_not_judged_by_pr007(
    db_engine: object,
    governance: GovernanceAgent,
    registry: SemanticLayerRegistry,
    verdict_for: VerdictFactory,
) -> None:
    entity = make_causal_entity()
    await registry.propose_draft(entity, await verdict_for(entity.id))
    payload = _approve_payload(entity.id, object_type="CausalEntity")
    result = await governance.check(payload, _state())
    assert result.verdict == "APPROVED", result.reason
    assert "PR-007" not in result.policy_rules_cited


async def test_an_assumptions_sensitivity_result_is_stored_and_read_back(
    db_engine: object,
    registry: SemanticLayerRegistry,
    verdict_for: VerdictFactory,
    kernel_sensitivity: SensitivityResult,
) -> None:
    """The write path PR-007 depends on: propose_draft used to drop the result."""
    assumption = _assumption(kernel_sensitivity)
    await registry.propose_draft(assumption, await verdict_for(assumption.id))
    stored = await registry.get_assumption(assumption.id)
    assert stored.sensitivity_result is not None
    assert stored.sensitivity_result.e_value == pytest.approx(kernel_sensitivity.e_value)
    assert stored.sensitivity_result.robust_to_unmeasured_confounding is (
        kernel_sensitivity.robust_to_unmeasured_confounding
    )


# ── Through the API: /check then /approve ────────────────────────────────────

_USER: dict[str, Any] = {
    "uid": "pr007-officer",
    "email": "pr007@test.com",
    "roles": ["governance_officer"],
}


@pytest_asyncio.fixture
async def gov_client(db_engine: object) -> AsyncGenerator[AsyncClient, None]:
    from src.api.main import app

    with patch(
        "src.middleware.auth._verify_firebase_token",
        new_callable=AsyncMock,
        return_value=_USER,
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={"Authorization": "Bearer pr007-token"},
        ) as ac:
            yield ac


async def _check(client: AsyncClient, edge_id: UUID) -> dict[str, Any]:
    resp = await client.post(
        "/api/governance/check",
        json={
            "action_type": GOVERNANCE_APPROVE,
            "payload": {"object_id": str(edge_id), "object_type": "CausalEdge"},
            "subject_id": str(edge_id),
            "cynefin_domain": "COMPLICATED",
        },
    )
    assert resp.status_code == 200, resp.text
    body: dict[str, Any] = resp.json()
    return body


async def test_the_review_flow_cannot_approve_an_edge_without_a_result(
    gov_client: AsyncClient,
    registry: SemanticLayerRegistry,
    verdict_for: VerdictFactory,
) -> None:
    edge = await _edge(registry, verdict_for, _assumption(None))
    body = await _check(gov_client, edge.id)
    assert body["verdict"] == "REJECTED"
    assert body["policy_rules_cited"] == ["PR-007"]

    approve = await gov_client.post(
        f"/api/governance/approve/{edge.id}",
        json={"object_type": "CausalEdge", "verdict_id": body["audit_entry_id"]},
    )
    assert approve.status_code == 403
    assert (await registry.get_edge(edge.id)).approval_status == "DRAFT"


async def test_the_review_flow_approves_an_edge_with_a_recorded_result(
    gov_client: AsyncClient,
    registry: SemanticLayerRegistry,
    verdict_for: VerdictFactory,
    db_session: AsyncSession,
    kernel_sensitivity: SensitivityResult,
) -> None:
    edge = await _edge(registry, verdict_for, _assumption(None))
    await _record(registry, db_session, edge.id, kernel_sensitivity)
    body = await _check(gov_client, edge.id)
    assert body["verdict"] == "APPROVED", body["reason"]

    approve = await gov_client.post(
        f"/api/governance/approve/{edge.id}",
        json={"object_type": "CausalEdge", "verdict_id": body["audit_entry_id"]},
    )
    assert approve.status_code == 200, approve.text
    assert (await registry.get_edge(edge.id)).approval_status == "APPROVED"
