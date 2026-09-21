"""
An agent cannot record a verdict the policy evaluation did not reach.

The register's `guardian-audit-write-skill` entry, found while fixing verdict
binding. `GovernanceAgent._execute_skill` exposed an `audit_write` skill whose
verdict came from its own inputs:

    verdict=GuardianVerdict(inputs.get("verdict", "APPROVED")),

So an agent asked to submit its output for review could instead write down that
it had passed. `docs/AGENTS.md` says "Every agent output passes the Guardian
policy engine before persistence"; this skill was a way round the engine.

The skill still writes a row, because a request to record something is worth
recording. It writes it as PENDING -- nobody has ruled on this -- and unsigned,
so it can authorise nothing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from sqlalchemy import text

from src.semantic_layer.models import CynefinDomain, EpistemicState, GuardianVerdict

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from src.agents.governance import GovernanceAgent

pytestmark = [pytest.mark.asyncio, pytest.mark.guardian]


def _state() -> EpistemicState:
    return EpistemicState(
        session_id="audit-write-test",
        agent_id="test-agent",
        cynefin_domain=CynefinDomain.COMPLICATED,
    )


async def test_the_skill_cannot_claim_approval(
    governance: GovernanceAgent, db_session: AsyncSession, db_engine: object
) -> None:
    """Asking for APPROVED must not produce APPROVED."""
    result = await governance._execute_skill(
        "audit_write",
        {"action_type": "SELF_CERTIFY", "verdict": "APPROVED"},
        _state(),
    )

    row = await db_session.execute(
        text("SELECT guardian_verdict, signature, policy_rules_cited FROM audit_log WHERE id = :i"),
        {"i": result["audit_entry_id"]},
    )
    record = row.mappings().one()
    assert record["guardian_verdict"] == "PENDING"
    assert record["signature"] is None, "an unruled row must not be signed"
    assert record["policy_rules_cited"] in ("[]", [])


async def test_the_skill_says_it_did_not_rule(
    governance: GovernanceAgent, db_engine: object
) -> None:
    """The caller is told, rather than left to infer it from a row they may not read."""
    result = await governance._execute_skill("audit_write", {"notes": "please note"}, _state())
    assert result["verdict"] == str(GuardianVerdict.PENDING)
    assert "guardian_check" in result["note"]


async def test_the_note_is_kept(
    governance: GovernanceAgent, db_session: AsyncSession, db_engine: object
) -> None:
    result = await governance._execute_skill(
        "audit_write", {"notes": "ran the migration by hand"}, _state()
    )
    row = await db_session.execute(
        text("SELECT notes FROM audit_log WHERE id = :i"), {"i": result["audit_entry_id"]}
    )
    assert row.scalar() == "ran the migration by hand"


async def test_a_ruling_still_comes_from_guardian_check(
    governance: GovernanceAgent, db_engine: object
) -> None:
    """The way to get a verdict is to be evaluated, which still works."""
    result = await governance._execute_skill(
        "guardian_check", {"action_type": "PROPOSE_DRAFT"}, _state()
    )
    assert str(result["verdict"]) == "APPROVED"
    assert result["audit_entry_id"]
