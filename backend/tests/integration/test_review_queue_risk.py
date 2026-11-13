"""
The review queue is ordered by risk tier and says who submitted each item.

This is the test the register's `review-queue-risk-tier` entry named (septa-36).
`GET /api/governance/review-queue` was summarised "List pending drafts ordered by risk
tier", and docs/UIX.md promised a queue "ordered by risk tier" -- but ReviewQueueItem
carried no tier and no submitter, the queue was ordered oldest-first, and the page
filled riskTier "MEDIUM" and submittedBy "system" for every item.

The tier is derived from what approving the object would change, read from stored
fields only (src/semantic_layer/review_risk.py holds the rules; these are them):

  * CausalEdge -- an approved edge changes the graph every estimate over it is
    identified from.
      CRITICAL  its stated confidence is below 0.5;
      HIGH      confidence below 0.8, or no sensitivity result the platform
                recorded on an assumption it rests on says it is robust to unmeasured
                confounding (the results PR-007 reads: recorded by
                record_edge_sensitivity under a RECORD_SENSITIVITY verdict for the
                edge; a result a drafter attached does not count);
      MEDIUM    otherwise.
  * GraphFragment -- approving it makes it usable by simulations.
      HIGH      it names an edge that is not APPROVED;
      MEDIUM    otherwise.
  * CausalEntity -- a node; it asserts no causal relationship.
      LOW

Preconditions: DRAFT objects in their tables; a governance-role caller.

Postconditions:
  * every item carries `risk_tier` (LOW | MEDIUM | HIGH | CRITICAL) by the rules above
    and `risk_reasons` saying which rule placed it;
  * items are ordered CRITICAL, HIGH, MEDIUM, LOW, oldest first within a tier;
  * `submitted_by` is the id of the user recorded as the object's creator
    (`created_by`), for edges too, and null when none was recorded.

Undefined here: the order of two items of one tier created at the same instant, and the
wording of `risk_reasons` beyond naming the deciding fact.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from src.semantic_layer.models import (
    Assumption,
    AssumptionType,
    CausalEdge,
    CausalEntity,
    CausalObjectRef,
    EdgeType,
    ElicitedFrom,
    EntityType,
    GraphFragment,
    SensitivityResult,
    UserRef,
)
from src.semantic_layer.verdict import RECORD_SENSITIVITY
from tests.conftest import issue_verdict_for, make_data_source

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from sqlalchemy.ext.asyncio import AsyncSession

    from src.semantic_layer.registry import SemanticLayerRegistry
    from tests.conftest import VerdictFactory

pytestmark = pytest.mark.asyncio

T0 = datetime(2026, 9, 1, tzinfo=UTC)

_USER: dict[str, Any] = {
    "uid": "queue-officer",
    "email": "queue@test.com",
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
            headers={"Authorization": "Bearer queue-token"},
        ) as ac:
            yield ac


def _user(uid: str) -> UserRef:
    return UserRef(id=uid, email=f"{uid}@test.com", name=uid)


def _sensitivity(robust: bool) -> SensitivityResult:
    return SensitivityResult(
        e_value=3.1 if robust else 1.2,
        robust_to_unmeasured_confounding=robust,
        analysis_method="e_value",
        computed_at=T0,
        computed_by="causal-core",
    )


async def _assumption(
    registry: SemanticLayerRegistry, verdict_for: VerdictFactory, robust: bool | None
) -> Assumption:
    assumption = Assumption(
        statement="No unmeasured cause of both the treatment and the outcome",
        assumption_type=AssumptionType.IGNORABILITY,
        justification="Measured common causes only",
        elicited_from=ElicitedFrom.AGENT,
        elicited_by=_user("elicitor"),
        domain="devops",
        sensitivity_result=None if robust is None else _sensitivity(robust),
    )
    await registry.propose_draft(assumption, await verdict_for(assumption.id))
    return assumption


async def _edge(
    registry: SemanticLayerRegistry,
    verdict_for: VerdictFactory,
    *,
    confidence: float,
    submitter: str | None,
    assumptions: tuple[Assumption, ...] = (),
) -> CausalEdge:
    edge = CausalEdge(
        source=CausalObjectRef(id=uuid4(), object_type="CausalEntity"),
        target=CausalObjectRef(id=uuid4(), object_type="Outcome"),
        edge_type=EdgeType.CAUSES,
        confidence=confidence,
        assumption_refs=[a.id for a in assumptions],
        guardian_verdict_ref=uuid4(),
        domain="devops",
        source_data=make_data_source(),
        **({"created_by": _user(submitter)} if submitter else {}),
    )
    await registry.propose_draft(edge, await verdict_for(edge.id))
    return edge


async def _entity(
    registry: SemanticLayerRegistry, verdict_for: VerdictFactory, submitter: str, age: int
) -> CausalEntity:
    entity = CausalEntity(
        name=f"service-{submitter}",
        entity_type=EntityType.SERVICE,
        domain="devops",
        ontology_version="1.0.0",
        created_by=_user(submitter),
        created_at=T0 - timedelta(days=age),
        source_data=make_data_source(),
    )
    await registry.propose_draft(entity, await verdict_for(entity.id))
    return entity


async def _fragment(
    registry: SemanticLayerRegistry,
    verdict_for: VerdictFactory,
    submitter: str,
    edges: list[CausalEdge],
) -> GraphFragment:
    fragment = GraphFragment(
        name=f"fragment-{submitter}",
        domain="devops",
        edges=[e.id for e in edges],
        created_by=_user(submitter),
        created_at=T0,
        source_data=make_data_source(),
    )
    await registry.propose_draft(fragment, await verdict_for(fragment.id))
    return fragment


async def _queue(client: AsyncClient) -> dict[str, dict[str, Any]]:
    resp = await client.get("/api/governance/review-queue")
    assert resp.status_code == 200, resp.text
    return {item["id"]: item for item in resp.json()}


async def _record(
    registry: SemanticLayerRegistry, db_session: AsyncSession, edge: CausalEdge, robust: bool
) -> None:
    """Record a sensitivity result on the edge the platform's way (septa-70)."""
    verdict = await issue_verdict_for(db_session, edge.id, action_type=RECORD_SENSITIVITY)
    await registry.record_edge_sensitivity(edge.id, _sensitivity(robust), verdict, _user("sim"))


async def test_edges_are_tiered_by_confidence_and_sensitivity(
    gov_client: AsyncClient,
    registry: SemanticLayerRegistry,
    verdict_for: VerdictFactory,
    db_session: AsyncSession,
) -> None:
    doubtful = await _edge(registry, verdict_for, confidence=0.4, submitter="ana")
    middling = await _edge(registry, verdict_for, confidence=0.7, submitter="ben")
    await _record(registry, db_session, middling, robust=True)
    unassessed = await _edge(registry, verdict_for, confidence=0.9, submitter="cy")
    not_robust = await _edge(registry, verdict_for, confidence=0.9, submitter="di")
    await _record(registry, db_session, not_robust, robust=False)
    solid = await _edge(registry, verdict_for, confidence=0.9, submitter="ed")
    await _record(registry, db_session, solid, robust=False)
    await _record(registry, db_session, solid, robust=True)

    queue = await _queue(gov_client)
    tiers = {e.id: queue[str(e.id)]["risk_tier"] for e in
             (doubtful, middling, unassessed, not_robust, solid)}
    assert tiers == {
        doubtful.id: "CRITICAL",
        middling.id: "HIGH",
        unassessed.id: "HIGH",
        not_robust.id: "HIGH",
        solid.id: "MEDIUM",
    }
    assert any("confidence" in r for r in queue[str(doubtful.id)]["risk_reasons"])
    assert any("sensitivity" in r for r in queue[str(unassessed.id)]["risk_reasons"])


async def test_a_drafter_attached_robust_result_does_not_lower_the_tier(
    gov_client: AsyncClient,
    registry: SemanticLayerRegistry,
    verdict_for: VerdictFactory,
) -> None:
    """The tier reads the results PR-007 reads: the drafter's own number is not one."""
    claimed = await _assumption(registry, verdict_for, robust=True)
    edge = await _edge(
        registry, verdict_for, confidence=0.9, submitter="fe", assumptions=(claimed,)
    )
    queue = await _queue(gov_client)
    assert queue[str(edge.id)]["risk_tier"] == "HIGH"
    assert any("recorded" in r for r in queue[str(edge.id)]["risk_reasons"])


async def test_fragments_and_entities_are_tiered_by_what_approval_changes(
    gov_client: AsyncClient,
    registry: SemanticLayerRegistry,
    verdict_for: VerdictFactory,
) -> None:
    approved_edge = await _edge(registry, verdict_for, confidence=0.9, submitter="fa")
    await registry.promote_to_approved(
        approved_edge.id,
        "CausalEdge",
        await verdict_for(approved_edge.id, action_type="GOVERNANCE_APPROVE"),
        _user("reviewer"),
    )
    draft_edge = await _edge(registry, verdict_for, confidence=0.9, submitter="fb")

    carries_draft = await _fragment(registry, verdict_for, "gi", [approved_edge, draft_edge])
    all_approved = await _fragment(registry, verdict_for, "ha", [approved_edge])
    entity = await _entity(registry, verdict_for, "io", age=0)

    queue = await _queue(gov_client)
    assert queue[str(carries_draft.id)]["risk_tier"] == "HIGH"
    assert queue[str(all_approved.id)]["risk_tier"] == "MEDIUM"
    assert queue[str(entity.id)]["risk_tier"] == "LOW"
    assert str(approved_edge.id) not in queue  # approved: no longer pending


async def test_the_queue_is_ordered_by_tier_then_oldest_first(
    gov_client: AsyncClient,
    registry: SemanticLayerRegistry,
    verdict_for: VerdictFactory,
) -> None:
    # Created in the reverse of the order the queue must show them in.
    newer_entity = await _entity(registry, verdict_for, "newer", age=1)
    older_entity = await _entity(registry, verdict_for, "older", age=5)
    medium = await _fragment(registry, verdict_for, "frag", [])
    high = await _edge(registry, verdict_for, confidence=0.9, submitter="hi")
    critical = await _edge(registry, verdict_for, confidence=0.2, submitter="cr")

    resp = await gov_client.get("/api/governance/review-queue")
    order = [item["id"] for item in resp.json()]
    assert order == [
        str(critical.id), str(high.id), str(medium.id), str(older_entity.id),
        str(newer_entity.id),
    ]
    assert [item["risk_tier"] for item in resp.json()] == [
        "CRITICAL", "HIGH", "MEDIUM", "LOW", "LOW",
    ]


async def test_each_item_says_who_submitted_it(
    gov_client: AsyncClient,
    registry: SemanticLayerRegistry,
    verdict_for: VerdictFactory,
    db_session: AsyncSession,
) -> None:
    edge = await _edge(registry, verdict_for, confidence=0.9, submitter="edge-author")
    anonymous = await _edge(registry, verdict_for, confidence=0.9, submitter=None)
    entity = await _entity(registry, verdict_for, "entity-author", age=0)
    fragment = await _fragment(registry, verdict_for, "fragment-author", [])

    queue = await _queue(gov_client)
    assert queue[str(edge.id)]["submitted_by"] == "edge-author"
    assert queue[str(entity.id)]["submitted_by"] == "entity-author"
    assert queue[str(fragment.id)]["submitted_by"] == "fragment-author"
    assert queue[str(anonymous.id)]["submitted_by"] is None

    # The edge's submitter is stored with the edge, not inferred for the response.
    row = await db_session.execute(
        text("SELECT created_by FROM causal_edges WHERE id = :i"), {"i": str(edge.id)}
    )
    assert "edge-author" in str(row.scalar_one())


# ── What an item shows of the edge's assumptions and sensitivity (septa-88) ──────
#
# The page's ReviewQueueItem declared evidenceCitations, sensitivityResults and
# assumptions that the endpoint never sent, so every live item read "No sensitivity
# tests run yet" -- also for an edge whose recorded robust result had just put it in
# MEDIUM. The backend stores both: the assumptions an edge references (`assumptions`)
# and the sensitivity results the platform recorded for it. It stores no evidence to
# cite (evidence_refs are ids into nothing), so evidence citations were dropped.
#
# Postconditions:
#   * an edge item's `assumptions` are the stored assumptions it references, each
#     with its id, statement, type and review status, in the edge's order;
#   * its `sensitivity_results` are the results the platform recorded for it --
#     a result a drafter attached to an assumption is not one -- with their stored
#     e-value, method and robustness;
#   * an assumption outside the reviewer's domain scope is not shown;
#   * fragment and entity items carry both lists, empty.


async def test_an_edge_item_shows_its_assumptions_and_recorded_sensitivity(
    gov_client: AsyncClient,
    registry: SemanticLayerRegistry,
    verdict_for: VerdictFactory,
    db_session: AsyncSession,
) -> None:
    drafted = await _assumption(registry, verdict_for, robust=False)
    edge = await _edge(
        registry, verdict_for, confidence=0.9, submitter="as", assumptions=(drafted,)
    )
    await _record(registry, db_session, edge, robust=True)

    item = (await _queue(gov_client))[str(edge.id)]
    ids = [a["id"] for a in item["assumptions"]]
    assert len(ids) == 2 and ids[0] == str(drafted.id)
    first = item["assumptions"][0]
    assert first["statement"] == drafted.statement
    assert first["assumption_type"] == "IGNORABILITY"
    assert first["review_status"] == "PENDING"

    # Only the recorded result: the drafter's (e-value 1.2, not robust) is not one.
    assert len(item["sensitivity_results"]) == 1
    recorded = item["sensitivity_results"][0]
    assert recorded["e_value"] == pytest.approx(3.1)
    assert recorded["robust_to_unmeasured_confounding"] is True
    assert recorded["analysis_method"] == "e_value"
    assert recorded["computed_by"] == "causal-core"
    assert item["risk_tier"] == "MEDIUM"


async def test_fragment_and_entity_items_carry_empty_lists(
    gov_client: AsyncClient,
    registry: SemanticLayerRegistry,
    verdict_for: VerdictFactory,
) -> None:
    fragment = await _fragment(registry, verdict_for, "lists", [])
    entity = await _entity(registry, verdict_for, "lists", age=0)
    queue = await _queue(gov_client)
    for obj in (fragment, entity):
        assert queue[str(obj.id)]["assumptions"] == []
        assert queue[str(obj.id)]["sensitivity_results"] == []


async def test_an_assumption_outside_the_reviewers_domains_is_not_shown(
    db_engine: object,
    registry: SemanticLayerRegistry,
    verdict_for: VerdictFactory,
) -> None:
    from src.api.main import app

    elsewhere = Assumption(
        statement="Marketing spend is not chosen with churn in view",
        assumption_type=AssumptionType.IGNORABILITY,
        justification="Budget set annually",
        elicited_from=ElicitedFrom.AGENT,
        elicited_by=_user("elicitor"),
        domain="marketing",
    )
    await registry.propose_draft(elsewhere, await verdict_for(elsewhere.id))
    edge = await _edge(
        registry, verdict_for, confidence=0.9, submitter="sc", assumptions=(elsewhere,)
    )
    scoped = {**_USER, "domains": ["devops"]}
    with patch(
        "src.middleware.auth._verify_firebase_token",
        new_callable=AsyncMock,
        return_value=scoped,
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={"Authorization": "Bearer queue-token"},
        ) as client:
            item = (await _queue(client))[str(edge.id)]
    assert item["assumptions"] == []
