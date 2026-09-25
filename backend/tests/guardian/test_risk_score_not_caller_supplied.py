"""
The numbers Guardian judges are not the caller's to choose.

This is the test the register's `guardian-risk-score-caller-supplied` entry named.
`POST /api/governance/check` copied `hallucination_risk_score` and `cycle_count`
straight from the request body into the policy state, so rule PR-002 judged a score
the caller picked and PR-005 counted cycles the caller declared. Any authenticated
caller could obtain a signed APPROVED verdict for a payload by sending 0.0 and 0.

Now:

* the score PR-002 reads is computed by the platform from the narrative being checked
  and the kernel outcome it reports (`src.agents.grounding_risk`), and a request that
  still sends a score -- or a cycle count -- is refused with 422, not silently obeyed;
* the cycle count PR-005 reads, and the domain PR-003/PR-006 read, come from the named
  server-side session when there is one.

The outcomes here are real `causal_core.analyze` results on data simulated with a
known effect, not hand-written dictionaries, so "grounded" means grounded in what the
kernel actually returned.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import numpy as np
import pytest
import pytest_asyncio
from causal_core import analyze
from httpx import ASGITransport, AsyncClient

from src.agents.grounding_risk import GROUNDED, UNGROUNDED, grounding_risk
from src.api.main import app
from src.orchestration.session_store import SessionStateStore
from src.semantic_layer.models import CynefinDomain, EpistemicState

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

pytestmark = pytest.mark.guardian

_USER: dict[str, Any] = {
    "uid": "risk-score-test",
    "email": "risk@test.com",
    "roles": ["governance_officer"],
}
GRAPH = "load -> playbook; load -> minutes; playbook -> minutes"


def _outcome(effect: float) -> dict[str, Any]:
    """A kernel outcome for a playbook whose true effect on minutes is ``effect``."""
    rng = np.random.default_rng(11)
    n = 2000
    load = rng.normal(size=n)
    playbook = (rng.random(n) < 1 / (1 + np.exp(-load))).astype(float)
    minutes = 10 + 2 * load + effect * playbook + rng.normal(size=n)
    out = analyze(
        {"load": load, "playbook": playbook, "minutes": minutes},
        GRAPH,
        treatment="playbook",
        outcome="minutes",
    )
    return out.model_dump(mode="json")


@pytest.fixture(scope="module")
def supported() -> dict[str, Any]:
    out = _outcome(-3.0)
    assert out["kind"] == "result" and out["status"] == "supported", out
    return out


@pytest_asyncio.fixture
async def client(db_engine: object) -> AsyncGenerator[AsyncClient, None]:
    with patch(
        "src.middleware.auth._verify_firebase_token",
        new_callable=AsyncMock,
        return_value=_USER,
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={"Authorization": "Bearer risk-test-token"},
        ) as ac:
            yield ac


async def _check(client: AsyncClient, **fields: Any) -> dict[str, Any]:
    body = {
        "action_type": "PUBLISH_EXPLANATION",
        "payload": {},
        "subject_id": str(uuid4()),
        "cynefin_domain": "COMPLICATED",
        **fields,
    }
    resp = await client.post("/api/governance/check", json=body)
    assert resp.status_code == 200, resp.text
    result: dict[str, Any] = resp.json()
    return result


# ── the score is computed ─────────────────────────────────────────────────────


def test_prose_bound_to_the_outcome_scores_zero(supported: dict[str, Any]) -> None:
    risk = grounding_risk("The playbook changed minutes by [[claim:effect]].", supported)
    assert risk.score == GROUNDED, risk.reasons


def test_a_number_the_outcome_does_not_contain_scores_one(supported: dict[str, Any]) -> None:
    risk = grounding_risk("The playbook cut minutes by 7.5.", supported)
    assert risk.score == UNGROUNDED
    assert "7.5" in " ".join(risk.reasons)


def test_prose_with_figures_and_no_outcome_is_ungrounded() -> None:
    risk = grounding_risk("The playbook caused a drop of 3 minutes.", None)
    assert risk.score == UNGROUNDED
    assert any("caused" in r for r in risk.reasons)


def test_causal_wording_needs_a_supported_result() -> None:
    refusal = analyze({"x": [0.0, 1.0], "y": [1.0, 2.0]}, "x -> y", treatment="x", outcome="y")
    assert refusal.kind == "refusal"
    risk = grounding_risk("Rolling out x will reduce y.", refusal.model_dump(mode="json"))
    assert risk.score == UNGROUNDED


def test_an_outcome_that_is_not_a_kernel_outcome_is_ungrounded() -> None:
    risk = grounding_risk("Minutes fell.", {"kind": "result", "effect": -3})
    assert risk.score == UNGROUNDED


def test_no_narrative_has_nothing_to_score() -> None:
    assert grounding_risk(None, None).score == GROUNDED
    assert grounding_risk("   ", None).score == GROUNDED


# ── /check: a declared score is refused, and the computed one decides ─────────


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["hallucination_risk_score", "cycle_count"])
async def test_a_self_assessment_in_the_body_is_refused(
    client: AsyncClient, field: str
) -> None:
    resp = await client.post(
        "/api/governance/check",
        json={
            "action_type": "PUBLISH_EXPLANATION",
            "payload": {},
            "cynefin_domain": "COMPLICATED",
            field: 0,
        },
    )
    assert resp.status_code == 422
    assert "computed by the platform" in resp.text


@pytest.mark.asyncio
async def test_a_high_risk_output_is_refused_whatever_the_caller_would_claim(
    client: AsyncClient, supported: dict[str, Any]
) -> None:
    """The case the register names: a low declared score used to buy an APPROVED verdict."""
    body = await _check(
        client, narrative="The playbook cut minutes by 7.5.", outcome=supported
    )
    assert body["verdict"] == "REJECTED"
    assert body["policy_rules_cited"] == ["PR-002"]
    assert "7.5" in body["reason"]

    unbacked = await _check(client, narrative="The playbook caused a drop of 3 minutes.")
    assert unbacked["verdict"] == "REJECTED"
    assert unbacked["policy_rules_cited"] == ["PR-002"]


@pytest.mark.asyncio
async def test_a_grounded_output_is_approved(
    client: AsyncClient, supported: dict[str, Any]
) -> None:
    body = await _check(
        client,
        narrative="The playbook changed minutes by [[claim:effect]] "
        "([[claim:effect.ci.lo]] to [[claim:effect.ci.hi]]).",
        outcome=supported,
    )
    assert body["verdict"] == "APPROVED", body["reason"]
    assert body["policy_rules_cited"] == []


# ── the session, not the body, says how many cycles and which domain ──────────


@pytest.mark.asyncio
async def test_the_cycle_count_is_the_sessions(client: AsyncClient) -> None:
    sid = f"risk-test-{uuid4()}"
    await SessionStateStore().save(
        EpistemicState(
            session_id=sid,
            agent_id="triage",
            cycle_count=5,
            cynefin_domain=CynefinDomain.COMPLICATED,
        )
    )
    body = await _check(client, session_id=sid)
    assert body["verdict"] == "REQUIRES_ESCALATION"
    assert body["policy_rules_cited"] == ["PR-005"]


@pytest.mark.asyncio
async def test_a_session_left_unclassified_is_not_reclassified_by_the_caller(
    client: AsyncClient,
) -> None:
    """The platform said DISORDER; the caller's COMPLICATED does not overrule it."""
    sid = f"risk-test-{uuid4()}"
    await SessionStateStore().save(
        EpistemicState(session_id=sid, agent_id="triage", cynefin_domain=CynefinDomain.DISORDER)
    )
    body = await _check(client, session_id=sid, cynefin_domain="COMPLICATED")
    assert body["verdict"] == "REJECTED"
    assert body["policy_rules_cited"] == ["PR-006"]
