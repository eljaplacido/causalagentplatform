"""
Guardian PR-010: a planner escalation cannot be approved.

PlanAhead (src/orchestration/planning.py, node_plan_ahead) runs a lookahead over the
candidate actions and, when none of them is safe to commit, escalates the run to a
person. Nothing in the Guardian knew that. A check carrying that escalation was
judged on its domain, its grounding score and its cycle count, and when those were
clean it came back APPROVED -- an approval of a run whose own planner had just said
there was no safe action to take.

The contract (documented in src/agents/governance.py, PR-010):

  The check's payload carries the planner's result under ``plan_outcome``. The value
  ``"no_safe_action"`` means the lookahead found no action it could commit. Whoever
  builds a check from a planned run -- the platform graph's GuardianCheck node --
  puts that value there.

Preconditions: a check whose state would otherwise be APPROVED (a classified,
COMPLICATED situation, no grounding risk, few cycles, no edge being approved), so
that no earlier rule decides first.

Postconditions:
  * ``plan_outcome == "no_safe_action"`` -> never APPROVED: the verdict is
    REQUIRES_ESCALATION (escalation routes to a person, AGENTS.md), PR-010 is cited,
    and that is what audit_log records;
  * any other plan_outcome (a committed action, no candidates, unsimulated) or none
    -> PR-010 does not fire.
  * A stale ``no_safe_action_found`` flag on the state is not the signal: a resumed
    session carries the flags of every earlier run, so a flag cannot say what *this*
    run's planner found.

Undefined here: which of two refusing rules is cited when another one also fires
(CHAOTIC, DISORDER, the cycle limit): each of those already refuses approval.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from sqlalchemy import text

from src.db.types import json_value
from src.semantic_layer.models import CynefinDomain, EpistemicState

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from src.agents.governance import GovernanceAgent

pytestmark = [pytest.mark.asyncio, pytest.mark.guardian]


def _state(**extra: Any) -> EpistemicState:
    return EpistemicState(
        session_id="pr010-test",
        agent_id="pr010-test",
        cynefin_domain=CynefinDomain.COMPLICATED,
        **extra,
    )


def _payload(plan_outcome: str | None) -> dict[str, Any]:
    return {
        "action_type": "GRAPH_NODE_GUARDIAN_CHECK",
        "proposed_edges": [],
        "recommendations": [{"rank": 1, "action": "restart the api gateway"}],
        "plan_outcome": plan_outcome,
    }


async def test_a_run_with_no_safe_action_is_not_approved(
    db_engine: object, governance: GovernanceAgent, db_session: AsyncSession
) -> None:
    result = await governance.check(_payload("no_safe_action"), _state())

    assert result.verdict != "APPROVED"
    assert result.verdict == "REQUIRES_ESCALATION", result.reason
    assert result.policy_rules_cited == ["PR-010"]
    assert "safe action" in result.reason

    row = (
        await db_session.execute(
            text("SELECT guardian_verdict, policy_rules_cited FROM audit_log WHERE id = :i"),
            {"i": str(result.audit_entry_id)},
        )
    ).one()
    assert row[0] == "REQUIRES_ESCALATION"
    assert list(json_value(row[1], [])) == ["PR-010"]


@pytest.mark.parametrize("outcome", [None, "committed", "no_actions", "unsimulated"])
async def test_other_plan_outcomes_are_not_refused_on_pr010(
    db_engine: object, governance: GovernanceAgent, outcome: str | None
) -> None:
    result = await governance.check(_payload(outcome), _state())
    assert result.verdict == "APPROVED", result.reason
    assert "PR-010" not in result.policy_rules_cited


async def test_a_flag_left_by_an_earlier_run_is_not_the_signal(
    db_engine: object, governance: GovernanceAgent
) -> None:
    state = _state(flags=["plan_ahead_escalation", "no_safe_action_found"])
    result = await governance.check(_payload("committed"), state)
    assert result.verdict == "APPROVED", result.reason
    assert "PR-010" not in result.policy_rules_cited


async def test_an_escalation_check_is_judged_the_same(
    db_engine: object, governance: GovernanceAgent
) -> None:
    """The action type the graph uses for a run it sent to a person does not matter:
    the rule reads plan_outcome, whatever the action."""
    result = await governance.check(
        {**_payload("no_safe_action"), "action_type": "GRAPH_HUMAN_ESCALATION"}, _state()
    )
    assert result.verdict == "REQUIRES_ESCALATION"
    assert result.policy_rules_cited == ["PR-010"]
