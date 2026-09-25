"""
Guardian bypass prevention tests — Priority 1, CI gate, must be 100% pass.

These tests validate the core invariants of the Guardian system:
  1. Semantic layer writes cannot proceed without an APPROVED verdict (DB constraint)
  2. A REJECTED verdict blocks semantic writes (GuardianBlockedError)
  3. audit_log rows cannot be updated (PermissionError / trigger-enforced)
  4. audit_log rows cannot be deleted (PermissionError / trigger-enforced)
  5. Every approved semantic write has a corresponding audit_log entry

From TESTING_CICD.md §4.1 and §4.2.
Marked with @pytest.mark.guardian for selective CI execution.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.types import TIMESTAMP, UUID_ARRAY, typed_text
from src.exceptions import GuardianBlockedError
from src.semantic_layer.models import (
    CynefinDomain,
    GuardianVerdict,
)
from tests.conftest import VerdictFactory, make_causal_edge, make_epistemic_state

pytestmark = pytest.mark.guardian


# ── 1. Direct DB write without verdict raises IntegrityError ──────────────────


#: A causal_edges row written straight to the table, bypassing the registry. The
#: array columns are bound as arrays (UUID[] on PostgreSQL, JSON text on SQLite),
#: so what refuses the row is the verdict FK the tests are about, not its shape.
_INSERT_EDGE = typed_text(
    "INSERT INTO causal_edges "
    "(id, source_id, source_type, target_id, target_type, "
    " edge_type, direction, confidence, evidence_refs, assumption_refs, "
    " approval_status, guardian_verdict_ref, version, lineage, domain, created_at) "
    "VALUES "
    "(:id, :src, 'CausalEntity', :tgt, 'Outcome', "
    " 'CAUSES', 'FORWARD', :confidence, :refs, :refs, "
    " 'DRAFT', :verdict_ref, '1.0.0', '[]', 'devops', :ts)",
    refs=UUID_ARRAY,
    ts=TIMESTAMP,
)


@pytest.mark.asyncio
async def test_semantic_layer_write_blocked_without_verdict(
    db_session: AsyncSession,
) -> None:
    """
    Direct INSERT into causal_edges without a guardian_verdict_ref must fail
    at the database level with an IntegrityError.

    This validates the FK constraint on causal_edges.guardian_verdict_ref:
    it references audit_log(id), which means a non-existent verdict_ref
    causes an integrity violation.

    From TESTING_CICD.md §4.1:
        'Direct write to semantic layer without Guardian verdict must be
         rejected at DB level.'
    """
    fake_verdict_id = str(uuid4())  # This ID does not exist in audit_log

    with pytest.raises((IntegrityError, OperationalError, Exception)) as exc_info:
        await db_session.execute(
            _INSERT_EDGE,
            {
                "id": str(uuid4()),
                "src": str(uuid4()),
                "tgt": str(uuid4()),
                "confidence": 0.9,
                "refs": [],
                "verdict_ref": fake_verdict_id,
                "ts": datetime.now(UTC).isoformat(),
            },
        )
        # SQLite deferred FK — commit triggers the constraint
        await db_session.commit()

    # Verify the error is an integrity violation (FK or constraint)
    error_msg = str(exc_info.value).lower()
    # The message varies by DB driver; check for common constraint indicators
    is_constraint_error = any(
        kw in error_msg
        for kw in [
            "foreign key",
            "integrity",
            "constraint",
            "not null",
            "reference",
            "fk_",
        ]
    ) or isinstance(exc_info.value, (IntegrityError, OperationalError))

    assert is_constraint_error, (
        f"Expected a constraint/integrity error when inserting without valid "
        f"guardian_verdict_ref, but got: {exc_info.value!r}"
    )


@pytest.mark.asyncio
async def test_semantic_layer_write_succeeds_with_approved_verdict(
    db_session: AsyncSession,
    approved_audit_entry_id: UUID,
) -> None:
    """
    Positive control: a write WITH a valid APPROVED verdict_ref must succeed.

    This ensures the FK constraint is working correctly (passes valid refs,
    blocks invalid ones).
    """
    await db_session.execute(
        _INSERT_EDGE,
        {
            "id": str(uuid4()),
            "src": str(uuid4()),
            "tgt": str(uuid4()),
            "confidence": 0.85,
            "refs": [],
            "verdict_ref": str(approved_audit_entry_id),
            "ts": datetime.now(UTC).isoformat(),
        },
    )
    await db_session.commit()

    written = await db_session.execute(
        text("SELECT COUNT(*) FROM causal_edges WHERE guardian_verdict_ref = :v"),
        {"v": str(approved_audit_entry_id)},
    )
    assert written.scalar_one() == 1


# ── 2. REJECTED verdict blocks registry write ─────────────────────────────────


@pytest.mark.asyncio
async def test_guardian_rejected_blocks_semantic_write(
    db_session: AsyncSession,
    rejected_audit_entry_id: UUID,
    registry: Any,
) -> None:
    """
    Attempting to call registry.propose_draft() with a REJECTED verdict_id
    must raise GuardianBlockedError.

    From TESTING_CICD.md §4.1:
        'A REJECTED Guardian verdict must prevent the action from completing.'
    """

    edge = make_causal_edge(rejected_audit_entry_id)

    with pytest.raises(GuardianBlockedError) as exc_info:
        await registry.propose_draft(edge, rejected_audit_entry_id)

    err = exc_info.value
    assert err.verdict_status == "REJECTED", (
        f"Expected verdict_status='REJECTED', got: {err.verdict_status}"
    )
    assert str(rejected_audit_entry_id) in str(err) or err.verdict_id == str(
        rejected_audit_entry_id
    ), "Exception should reference the blocked verdict ID"


@pytest.mark.asyncio
async def test_guardian_missing_verdict_blocks_semantic_write(
    db_session: AsyncSession,
    registry: Any,
) -> None:
    """
    Attempting to call registry.propose_draft() with a verdict_id that does
    not exist in audit_log must raise GuardianBlockedError.
    """

    nonexistent_verdict = uuid4()
    edge = make_causal_edge(nonexistent_verdict)

    with pytest.raises(GuardianBlockedError) as exc_info:
        await registry.propose_draft(edge, nonexistent_verdict)

    assert "not found" in str(exc_info.value).lower()


# ── 3. audit_log UPDATE is blocked ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_audit_log_no_update(
    db_session: AsyncSession,
    approved_audit_entry_id: UUID,
) -> None:
    """
    UPDATE on audit_log must be rejected.

    In PostgreSQL: the trigger fn_audit_log_no_mutation raises an exception.
    In SQLite (test environment): we simulate by verifying the row is
    unchanged after an attempted update, or by testing that the application
    layer refuses to issue UPDATEs.

    From TESTING_CICD.md §4.2:
        'UPDATE on audit_log table must be rejected by database role.'
    """
    # Verify the entry exists
    row = await db_session.execute(
        text("SELECT action_type FROM audit_log WHERE id = :id"),
        {"id": str(approved_audit_entry_id)},
    )
    original_action = row.scalar_one()

    # Attempt to UPDATE the row
    # In SQLite test env, the trigger is not present — we verify via application
    # invariant: GovernanceAgent never issues UPDATE on audit_log.
    # We also verify the GovernanceAgent._write_audit_entry only uses INSERT.
    import inspect
    import re

    from src.agents.governance import GovernanceAgent

    source = inspect.getsource(GovernanceAgent._write_audit_entry)
    assert re.search(r"UPDATE\s+audit_log", source, re.IGNORECASE) is None, (
        "GovernanceAgent._write_audit_entry must not contain UPDATE statements "
        "targeting audit_log"
    )
    assert "INSERT INTO audit_log" in source, (
        "GovernanceAgent._write_audit_entry must use INSERT to write audit entries"
    )

    # In the PostgreSQL production schema the trigger blocks it. Simulate here:
    # Try the update and verify the row is unchanged if SQLite silently allows it.
    try:
        await db_session.execute(
            text(
                "UPDATE audit_log SET action_type = 'TAMPERED' WHERE id = :id"
            ),
            {"id": str(approved_audit_entry_id)},
        )
        await db_session.commit()
    except Exception:
        # SQLite may raise; PostgreSQL definitely raises via trigger.
        # Either outcome is acceptable. Roll back so the re-read below runs: on
        # PostgreSQL the failed statement leaves the transaction aborted.
        await db_session.rollback()

    # Re-read and compare. Until 2026-09-24 this test captured `original_action`
    # above, attempted the UPDATE, and then stopped -- so whether the row had
    # actually been tampered with was never checked, and the assertion the test
    # is named for was not made. The static check above is about the application
    # code; this is about the store.
    after = (
        await db_session.execute(
            text("SELECT action_type FROM audit_log WHERE id = :id"),
            {"id": str(approved_audit_entry_id)},
        )
    ).scalar_one()
    assert after == original_action, (
        "the audit row was silently rewritten: the UPDATE neither raised nor was "
        f"refused, and action_type went from {original_action!r} to {after!r}. "
        "In the production schema 001_initial.sql's trigger blocks this; under the "
        "test's SQLite this assertion is what stands in for it."
    )


@pytest.mark.asyncio
async def test_audit_log_update_raises_in_production_schema() -> None:
    """
    Verify that the production DDL (001_initial.sql) includes the no-mutation
    trigger that raises an exception on UPDATE.

    This is a static analysis test — reads the migration file.
    """
    import os

    migration_path = os.path.join(
        os.path.dirname(__file__),
        "..",
        "..",
        "src",
        "db",
        "migrations",
        "001_initial.sql",
    )
    with open(os.path.normpath(migration_path)) as f:
        ddl = f.read()

    assert "fn_audit_log_no_mutation" in ddl, (
        "Migration must define fn_audit_log_no_mutation trigger function"
    )
    assert "BEFORE UPDATE ON audit_log" in ddl, (
        "Migration must include BEFORE UPDATE trigger on audit_log"
    )
    assert "BEFORE DELETE ON audit_log" in ddl, (
        "Migration must include BEFORE DELETE trigger on audit_log"
    )
    assert "RAISE EXCEPTION" in ddl, (
        "The mutation-prevention trigger must RAISE EXCEPTION"
    )


# ── 4. audit_log DELETE is blocked ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_audit_log_no_delete(
    db_session: AsyncSession,
    approved_audit_entry_id: UUID,
) -> None:
    """
    DELETE on audit_log must be rejected -- by the database, and by the code.

    This test used to be unable to fail: it looked for "DELETE FROM audit_log" in
    upper-cased source (the lowercase needle can never match), and swallowed any
    outcome of the DELETE it then attempted. Both halves now assert. The SQLite
    triggers in db/factory.py stand in for 001_initial.sql's under test.

    From TESTING_CICD.md §4.2:
        'DELETE on audit_log table must be rejected.'
    """
    import pathlib
    import re

    src = pathlib.Path(__file__).resolve().parents[2] / "src"
    offenders = [
        str(path.relative_to(src))
        for path in src.rglob("*.py")
        if re.search(r"DELETE\s+FROM\s+audit_log", path.read_text(), re.IGNORECASE)
    ]
    assert offenders == [], f"code that deletes audit rows: {offenders}"

    with pytest.raises(Exception, match=r"(?i)append-only|audit_log"):
        await db_session.execute(
            text("DELETE FROM audit_log WHERE id = :id"),
            {"id": str(approved_audit_entry_id)},
        )
        await db_session.commit()
    await db_session.rollback()

    still = await db_session.execute(
        text("SELECT COUNT(*) FROM audit_log WHERE id = :id"),
        {"id": str(approved_audit_entry_id)},
    )
    assert still.scalar_one() == 1, "the audit row was deleted"


# ── 5. Every semantic write has an audit entry ────────────────────────────────


@pytest.mark.asyncio
async def test_every_semantic_write_has_audit_entry(
    db_session: AsyncSession,
    approved_audit_entry_id: UUID,
    verdict_for: VerdictFactory,
    registry: Any,
) -> None:
    """
    Every approved semantic layer write must have a corresponding audit entry.

    Specifically:
      1. A valid APPROVED verdict entry exists in audit_log
      2. propose_draft() succeeds and the edge is persisted
      3. The audit_log entry referenced by the edge's guardian_verdict_ref
         has guardian_verdict = 'APPROVED'

    From TESTING_CICD.md §4.2:
        'Every approved semantic layer write must have a corresponding
         audit entry with guardian_verdict=APPROVED.'
    """
    edge = make_causal_edge(approved_audit_entry_id)
    edge_id = await registry.propose_draft(edge, await verdict_for(edge.id))

    # Verify the edge was persisted
    edge_row = await db_session.execute(
        text(
            "SELECT id, guardian_verdict_ref FROM causal_edges WHERE id = :eid"
        ),
        {"eid": str(edge_id)},
    )
    edge_record = edge_row.mappings().one_or_none()
    assert edge_record is not None, f"Edge {edge_id} was not found in causal_edges"

    verdict_ref = edge_record["guardian_verdict_ref"]

    # Verify the audit entry exists and is APPROVED
    audit_row = await db_session.execute(
        text(
            "SELECT guardian_verdict FROM audit_log WHERE id = :aid"
        ),
        {"aid": str(verdict_ref)},
    )
    audit_record = audit_row.mappings().one_or_none()
    assert audit_record is not None, (
        f"audit_log entry {verdict_ref} referenced by edge {edge_id} not found"
    )
    assert audit_record["guardian_verdict"] == "APPROVED", (
        f"Expected audit entry to have verdict=APPROVED, "
        f"got: {audit_record['guardian_verdict']}"
    )


@pytest.mark.asyncio
async def test_governance_agent_writes_audit_on_every_check(
    db_session: AsyncSession,
    governance: Any,
) -> None:
    """
    The GovernanceAgent must write an audit_log entry for EVERY check() call,
    regardless of the verdict (APPROVED, REJECTED, or REQUIRES_ESCALATION).
    """
    # Count entries before
    before = await db_session.execute(text("SELECT COUNT(*) FROM audit_log"))
    count_before = before.scalar() or 0

    state = make_epistemic_state()
    await governance.check(
        {"action_type": "TEST_CHECK", "test": True},
        state,
    )

    after = await db_session.execute(text("SELECT COUNT(*) FROM audit_log"))
    count_after = after.scalar() or 0

    assert count_after == count_before + 1, (
        f"Expected exactly 1 new audit_log entry after check(), "
        f"but count changed from {count_before} to {count_after}"
    )


@pytest.mark.asyncio
async def test_governance_agent_cannot_bypass_via_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    GovernanceAgent must raise RuntimeError if GUARDIAN_BYPASS_MODE is set.

    This is the defence-in-depth check that prevents misconfigured deployments.
    """
    monkeypatch.setenv("GUARDIAN_BYPASS_MODE", "true")

    with pytest.raises(RuntimeError) as exc_info:
        from src.agents.governance import GovernanceAgent as GA
        GA(agent_id="bypass-attempt")

    assert "bypass" in str(exc_info.value).lower(), (
        "RuntimeError message should mention 'bypass'"
    )


@pytest.mark.asyncio
async def test_governance_agent_cannot_bypass_via_kwarg() -> None:
    """
    GovernanceAgent's metaclass must reject any constructor kwarg containing
    the word 'bypass'.
    """
    with pytest.raises(TypeError) as exc_info:
        from src.agents.governance import GovernanceAgent as GA
        GA(agent_id="test", bypass_mode=True)  # type: ignore[call-arg]

    assert "bypass" in str(exc_info.value).lower()


# ── 12. Drift-triggered actions blocked from semantic writes ─────────────────


@pytest.mark.asyncio
async def test_drift_action_blocked_from_semantic_write(
    db_session: AsyncSession,
    governance: Any,
) -> None:
    """
    Actions initiated by drift detection must not be allowed to write to the
    semantic layer. PR-008 should reject such payloads.

    From AGENTS.md §2.1 DriftMonitorAgent:
        'NEVER write to the semantic layer (alert-only, no graph modifications)'
    """
    from tests.conftest import make_epistemic_state

    state = make_epistemic_state()
    # Must use a non-DISORDER domain so PR-006 doesn't fire before PR-008
    state = state.model_copy(update={"cynefin_domain": CynefinDomain.COMPLICATED})

    # Simulate a drift-triggered action that tries to propose edges
    drift_payload = {
        "action_type": "DRIFT_REVALIDATION",
        "proposed_edges": [
            {"source": {"id": "node-a"}, "target": {"id": "node-b"}},
        ],
    }

    result = await governance.check(drift_payload, state)

    assert result.verdict == GuardianVerdict.REJECTED, (
        f"Expected REJECTED for drift action with proposed_edges, got: {result.verdict}"
    )
    assert "PR-008" in result.policy_rules_cited, (
        f"Expected PR-008 to be cited, got: {result.policy_rules_cited}"
    )
