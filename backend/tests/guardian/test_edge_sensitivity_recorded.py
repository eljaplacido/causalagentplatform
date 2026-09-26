"""
The kernel's sensitivity analysis of an edge reaches the assumption PR-007 reads (septa-62).

Guardian PR-007 refuses to approve an edge unless an assumption it rests on carries a
stored SensitivityResult (septa-33). Nothing in the platform wrote one: SimulationAgent
computed the kernel's E-value and dropped it, and no endpoint ran the kernel on a stored
edge at all. So every edge whose drafter attached no result was blocked, whatever
analysis had been run.

**Which assumption an edge's sensitivity result attaches to.** An E-value (and a
Rosenbaum gamma*) says how strong unmeasured confounding of the edge's source and
target would have to be to explain the estimated effect away: it is evidence about the
edge's IGNORABILITY assumption, "no unmeasured common cause of source and target",
*as tested by that analysis*. The registry therefore records it as a NEW Assumption --
IGNORABILITY, PENDING review, elicited from AGENT, scoped to the edge's source and
target, carrying the SensitivityResult -- and adds it to the edge's assumption_refs.
It never rewrites an existing assumption (the drafter's stay what the drafter said, and
an ACCEPTED one is an approved object), and it never touches an APPROVED or DEPRECATED
edge. A reviewer who rejects the new assumption takes the result out of PR-007's count
(usable_sensitivity), as for any other.

**The governed path.** One Guardian check for action RECORD_SENSITIVITY whose subject is
the edge; the registry spends that verdict (signed, bound to the edge and the action,
single-use) in the same transaction as the insert and the edge's new reference, and
appends a MODIFIED lineage entry to the edge.

**Where the kernel runs on an edge's question.**
  * ``POST /api/semantic/edges/{edge_id}/sensitivity``: the caller supplies data (one
    column per node id) and the measured common causes to adjust for; the platform runs
    ``causal_core.analyze`` for source -> target.
  * SimulationAgent: its estimate of the fragment's Treatment -> Outcome effect answers
    the question of the fragment's edge(s) from that treatment to that outcome.
  * Not an edge's question: POST /api/v1/decisions (its graph names variables, not
    semantic-layer objects) and the planner (it passes no data).

Preconditions: a DRAFT edge stored through the registry; data simulated from a
structural model with a known effect (-3) and one measured confounder.

Postconditions:
  * after the analysis, the new assumption carries the kernel's own E-value (the same
    number ``causal_core.analyze`` gives on the same data and graph) and PR-007 no longer
    refuses: /check GOVERNANCE_APPROVE is APPROVED citing no PR-007, and /approve
    promotes the edge;
  * nothing is written when the kernel returns no sensitivity analysis (a refusal is a
    result, and is relayed), when the Guardian does not approve the recording, when the
    edge is already APPROVED, or when the verdict was issued for another action;
  * the drafter's own assumptions are not modified;
  * a Rosenbaum gamma* in the outcome, when the kernel reports one, is carried; when it
    does not (this kernel version), rosenbaum_gamma is None.

Undefined here: whether a result that says "not robust" should block approval (PR-007
asks for the analysis; the reviewer sees what it found).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import numpy as np
import pytest
from causal_core import analyze
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from src.agents.edge_sensitivity import RECORD_SENSITIVITY, sensitivity_from_outcome
from src.exceptions import GuardianBlockedError
from src.semantic_layer.models import (
    Assumption,
    AssumptionType,
    CausalEdge,
    CausalObjectRef,
    CynefinDomain,
    EdgeType,
    ElicitedFrom,
    EpistemicState,
    GraphFragment,
    SensitivityResult,
)
from tests.conftest import VerdictFactory, make_user_ref

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from src.agents.governance import GovernanceAgent
    from src.semantic_layer.registry import SemanticLayerRegistry

pytestmark = [pytest.mark.asyncio, pytest.mark.guardian]

GOVERNANCE_APPROVE = "GOVERNANCE_APPROVE"
TRUE_EFFECT = -3.0


def _simulate(treatment: str, outcome: str, confounder: str = "load") -> dict[str, list[float]]:
    """A playbook (treatment) cutting minutes (outcome) by 3, confounded by load."""
    rng = np.random.default_rng(11)
    n = 2000
    load = rng.normal(size=n)
    playbook = (rng.random(n) < 1 / (1 + np.exp(-load))).astype(float)
    minutes = 10 + 2 * load + TRUE_EFFECT * playbook + rng.normal(size=n)
    return {confounder: load.tolist(), treatment: playbook.tolist(), outcome: minutes.tolist()}


def _kernel_e_value(data: dict[str, list[float]], treatment: str, outcome: str) -> float:
    """What the kernel itself says, on the same data and graph, independently of the flow."""
    out = analyze(
        data,
        [("load", treatment), ("load", outcome), (treatment, outcome)],
        treatment=treatment,
        outcome=outcome,
    ).model_dump(mode="json")
    assert out["kind"] == "result", out
    assert out["primary"]["point"] == pytest.approx(TRUE_EFFECT, abs=0.25)
    e_value = out["sensitivity"]["e_value"]
    assert e_value is not None and e_value > 1
    return float(e_value)


def _state() -> EpistemicState:
    return EpistemicState(
        session_id="septa62-test", agent_id="septa62-test", cynefin_domain=CynefinDomain.COMPLICATED
    )


def _drafter_assumption() -> Assumption:
    return Assumption(
        statement="Load is the only common cause of the playbook and the minutes",
        assumption_type=AssumptionType.IGNORABILITY,
        justification="The on-call team says so",
        elicited_from=ElicitedFrom.DOMAIN_EXPERT,
        elicited_by=make_user_ref(),
        domain="devops",
    )


async def _draft_edge(
    registry: SemanticLayerRegistry,
    verdict_for: VerdictFactory,
    *assumptions: Assumption,
    source_type: str = "CausalEntity",
    target_type: str = "Outcome",
    source: UUID | None = None,
    target: UUID | None = None,
) -> CausalEdge:
    for assumption in assumptions:
        await registry.propose_draft(assumption, await verdict_for(assumption.id))
    edge = CausalEdge(
        source=CausalObjectRef(id=source or uuid4(), object_type=source_type),
        target=CausalObjectRef(id=target or uuid4(), object_type=target_type),
        edge_type=EdgeType.CAUSES,
        confidence=0.8,
        assumption_refs=[a.id for a in assumptions],
        guardian_verdict_ref=uuid4(),
        domain="devops",
    )
    await registry.propose_draft(edge, await verdict_for(edge.id))
    return edge


async def _count(db_session: AsyncSession, table: str) -> int:
    sql = {"assumptions": "SELECT COUNT(*) FROM assumptions"}[table]
    return int((await db_session.execute(text(sql))).scalar() or 0)


# ── The API flow, end to end ─────────────────────────────────────────────────

_AUTHOR: dict[str, Any] = {
    "uid": "septa62-author", "email": "a@test.com", "roles": ["graph_author"],
    "domains": ["devops"],
}
_OFFICER: dict[str, Any] = {
    "uid": "septa62-officer", "email": "o@test.com", "roles": ["governance_officer"],
}


def _client_as(user: dict[str, Any]) -> Any:
    class _Ctx:
        async def __aenter__(self) -> AsyncClient:
            self._patch = patch(
                "src.middleware.auth._verify_firebase_token",
                new_callable=AsyncMock,
                return_value=user,
            )
            self._patch.start()
            self._client = AsyncClient(
                transport=ASGITransport(app=_app()),
                base_url="http://test",
                headers={"Authorization": "Bearer septa62"},
            )
            return await self._client.__aenter__()

        async def __aexit__(self, *exc: object) -> None:
            await self._client.__aexit__(*exc)
            self._patch.stop()

    return _Ctx()


def _app() -> Any:
    from src.api.main import app

    return app


async def _analyse(edge: CausalEdge, body: dict[str, Any]) -> Any:
    async with _client_as(_AUTHOR) as client:
        return await client.post(f"/api/semantic/edges/{edge.id}/sensitivity", json=body)


def _body(edge: CausalEdge, **extra: Any) -> dict[str, Any]:
    return {
        "data": _simulate(str(edge.source.id), str(edge.target.id)),
        "adjust_for": ["load"],
        "cynefin_domain": "COMPLICATED",
        **extra,
    }


async def _approval_check(edge_id: UUID) -> dict[str, Any]:
    async with _client_as(_OFFICER) as client:
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


async def test_analysing_a_draft_edge_records_the_kernels_result_and_pr007_accepts_it(
    db_engine: object, registry: SemanticLayerRegistry, verdict_for: VerdictFactory
) -> None:
    drafter = _drafter_assumption()
    edge = await _draft_edge(registry, verdict_for, drafter)

    before = await _approval_check(edge.id)
    assert before["verdict"] == "REJECTED" and before["policy_rules_cited"] == ["PR-007"]

    body = _body(edge)
    expected = _kernel_e_value(body["data"], str(edge.source.id), str(edge.target.id))
    resp = await _analyse(edge, body)
    assert resp.status_code == 200, resp.text
    got = resp.json()
    assert got["outcome_kind"] == "result"
    assert got["guardian_verdict"] == "APPROVED"
    assert got["sensitivity"]["eValue"] == pytest.approx(expected)
    assert got["sensitivity"]["rosenbaumGamma"] is None  # not in this kernel's outcome
    new_id = UUID(got["assumption_id"])

    recorded = await registry.get_assumption(new_id)
    assert recorded.assumption_type == AssumptionType.IGNORABILITY
    assert recorded.review_status == "PENDING"
    assert recorded.elicited_from == ElicitedFrom.AGENT
    assert recorded.elicited_by.id == _AUTHOR["uid"]
    assert {r.id for r in recorded.scope} == {edge.source.id, edge.target.id}
    assert recorded.domain == "devops"
    assert recorded.sensitivity_result is not None
    assert recorded.sensitivity_result.e_value == pytest.approx(expected)

    stored = await registry.get_edge(edge.id)
    assert stored.approval_status == "DRAFT"
    assert stored.assumption_refs == [drafter.id, new_id]
    assert stored.lineage[-1].step_type == "MODIFIED"
    assert str(new_id) in stored.lineage[-1].description

    untouched = await registry.get_assumption(drafter.id)
    assert untouched.sensitivity_result is None

    after = await _approval_check(edge.id)
    assert after["verdict"] == "APPROVED", after["reason"]
    assert "PR-007" not in after["policy_rules_cited"]

    async with _client_as(_OFFICER) as client:
        approve = await client.post(
            f"/api/governance/approve/{edge.id}",
            json={"object_type": "CausalEdge", "verdict_id": after["audit_entry_id"]},
        )
    assert approve.status_code == 200, approve.text
    assert (await registry.get_edge(edge.id)).approval_status == "APPROVED"


async def test_an_approved_edge_is_not_changed(
    db_engine: object,
    registry: SemanticLayerRegistry,
    verdict_for: VerdictFactory,
    db_session: AsyncSession,
) -> None:
    edge = await _draft_edge(registry, verdict_for)
    await registry.promote_to_approved(
        edge.id,
        "CausalEdge",
        await verdict_for(edge.id, action_type=GOVERNANCE_APPROVE),
        make_user_ref(),
    )
    before = await _count(db_session, "assumptions")

    resp = await _analyse(edge, _body(edge))
    assert resp.status_code == 409, resp.text
    assert await _count(db_session, "assumptions") == before
    assert (await registry.get_edge(edge.id)).assumption_refs == []


async def test_an_unclassified_situation_records_nothing(
    db_engine: object,
    registry: SemanticLayerRegistry,
    verdict_for: VerdictFactory,
    db_session: AsyncSession,
) -> None:
    """No domain declared and no session: DISORDER, which PR-006 refuses."""
    edge = await _draft_edge(registry, verdict_for)
    before = await _count(db_session, "assumptions")
    body = _body(edge)
    body.pop("cynefin_domain")

    resp = await _analyse(edge, body)
    assert resp.status_code == 403, resp.text
    assert "PR-006" in resp.text
    assert await _count(db_session, "assumptions") == before
    assert (await registry.get_edge(edge.id)).assumption_refs == []


async def test_a_kernel_refusal_is_relayed_and_records_nothing(
    db_engine: object,
    registry: SemanticLayerRegistry,
    verdict_for: VerdictFactory,
    db_session: AsyncSession,
) -> None:
    edge = await _draft_edge(registry, verdict_for)
    body = _body(edge)
    body["data"][str(edge.source.id)] = [1.0] * len(body["data"]["load"])  # everyone treated
    before = await _count(db_session, "assumptions")

    resp = await _analyse(edge, body)
    assert resp.status_code == 200, resp.text
    got = resp.json()
    assert got["outcome_kind"] != "result"
    assert got["detail"]
    assert got["sensitivity"] is None and got["assumption_id"] is None
    assert got["guardian_verdict"] is None
    assert await _count(db_session, "assumptions") == before

    check = await _approval_check(edge.id)
    assert check["policy_rules_cited"] == ["PR-007"]


async def test_data_without_the_edges_columns_is_refused(
    db_engine: object, registry: SemanticLayerRegistry, verdict_for: VerdictFactory
) -> None:
    edge = await _draft_edge(registry, verdict_for)
    body = _body(edge)
    del body["data"][str(edge.target.id)]
    resp = await _analyse(edge, body)
    assert resp.status_code == 422, resp.text
    assert str(edge.target.id) in resp.text


# ── The registry's write: only on a verdict for this action on this edge ─────


def _result() -> SensitivityResult:
    return SensitivityResult(
        e_value=2.4,
        robust_to_unmeasured_confounding=True,
        analysis_method="test",
        computed_at=datetime.now(UTC),
        computed_by="causal-core",
    )


async def test_recording_needs_a_verdict_issued_for_recording(
    db_engine: object,
    registry: SemanticLayerRegistry,
    verdict_for: VerdictFactory,
    db_session: AsyncSession,
) -> None:
    edge = await _draft_edge(registry, verdict_for)
    before = await _count(db_session, "assumptions")
    with pytest.raises(GuardianBlockedError):
        await registry.record_edge_sensitivity(
            edge.id, _result(), await verdict_for(edge.id), make_user_ref()
        )
    with pytest.raises(GuardianBlockedError):
        await registry.record_edge_sensitivity(
            edge.id,
            _result(),
            await verdict_for(uuid4(), action_type=RECORD_SENSITIVITY),
            make_user_ref(),
        )
    assert await _count(db_session, "assumptions") == before

    verdict = await verdict_for(edge.id, action_type=RECORD_SENSITIVITY)
    new_id = await registry.record_edge_sensitivity(edge.id, _result(), verdict, make_user_ref())
    assert (await registry.get_edge(edge.id)).assumption_refs == [new_id]
    with pytest.raises(GuardianBlockedError):  # spent
        await registry.record_edge_sensitivity(edge.id, _result(), verdict, make_user_ref())


# ── Reading the kernel's outcome ─────────────────────────────────────────────


def test_a_rosenbaum_gamma_in_the_outcome_is_carried() -> None:
    outcome = {
        "kind": "result",
        "sensitivity": {
            "e_value": 3.1,
            "e_value_ci": 2.2,
            "e_value_method": "approximate",
            "rosenbaum": {"gamma_star": 1.8, "method": "pair matching"},
        },
    }
    got = sensitivity_from_outcome(outcome, computed_by="x")
    assert got is not None
    assert got.e_value == pytest.approx(3.1)
    assert got.rosenbaum_gamma == pytest.approx(1.8)
    assert got.robust_to_unmeasured_confounding is True


@pytest.mark.parametrize(
    "outcome",
    [
        {"kind": "abstention", "reason": "POSITIVITY_VIOLATION"},
        {"kind": "result", "sensitivity": None},
        {"kind": "result", "sensitivity": {"e_value": None, "rosenbaum": None}},
    ],
)
def test_no_sensitivity_analysis_means_no_result(outcome: dict[str, Any]) -> None:
    assert sensitivity_from_outcome(outcome, computed_by="x") is None


# ── SimulationAgent: its estimate answers the fragment's treatment -> outcome edge ──


async def test_simulation_records_the_sensitivity_of_the_edge_it_estimated(
    db_engine: object,
    registry: SemanticLayerRegistry,
    verdict_for: VerdictFactory,
    governance: GovernanceAgent,
) -> None:
    from src.agents.simulation import SimulationAgent

    treatment, outcome, load = uuid4(), uuid4(), uuid4()
    effect = await _draft_edge(
        registry, verdict_for, source=treatment, target=outcome,
        source_type="Treatment", target_type="Outcome",
    )
    to_treatment = await _draft_edge(
        registry, verdict_for, source=load, target=treatment, target_type="Treatment"
    )
    to_outcome = await _draft_edge(registry, verdict_for, source=load, target=outcome)
    fragment = GraphFragment(
        name="playbook fragment",
        domain="devops",
        edges=[effect.id, to_treatment.id, to_outcome.id],
        created_by=make_user_ref(),
    )
    await registry.propose_draft(fragment, await verdict_for(fragment.id))
    await registry.promote_to_approved(
        fragment.id,
        "GraphFragment",
        await verdict_for(fragment.id, action_type=GOVERNANCE_APPROVE),
        make_user_ref(),
    )

    data = _simulate(str(treatment), str(outcome), confounder=str(load))
    state = _state().model_copy(update={"active_graph_fragment_id": str(fragment.id)})
    final = await SimulationAgent().process(state, data=data)
    assert final.simulation_results, final.draft_explanation

    refs = (await registry.get_edge(effect.id)).assumption_refs
    assert len(refs) == 1
    recorded = await registry.get_assumption(refs[0])
    assert recorded.sensitivity_result is not None
    assert recorded.sensitivity_result.e_value is not None
    assert recorded.sensitivity_result.e_value > 1
    # Only the edge whose question the estimate answers.
    assert (await registry.get_edge(to_treatment.id)).assumption_refs == []
    assert (await registry.get_edge(to_outcome.id)).assumption_refs == []

    result = await governance.check(
        {
            "action_type": GOVERNANCE_APPROVE,
            "object_id": str(effect.id),
            "object_type": "CausalEdge",
            "subject_id": str(effect.id),
        },
        _state(),
    )
    assert result.verdict == "APPROVED", result.reason
    assert "PR-007" not in result.policy_rules_cited
