"""
GovernanceAgent (The Guardian) — cross-cutting policy enforcement agent.

This is the most critical agent in the platform. It cannot be bypassed.
It cannot be instantiated with a bypass flag. All verdicts are written to
the append-only audit_log before being returned.

Responsibilities (AGENTS.md §2.1 GovernanceAgent):
  - Validate: no cycle detection bypass, hallucination risk, policy rules
  - Return APPROVED / REJECTED / REQUIRES_ESCALATION
  - Write every verdict to audit_log (immutable)
  - Escalation routes to human — never to another agent

Security invariants (CYBERSECURITY.md §2.2, §7.1):
  - Cannot be instantiated with bypass_mode
  - Every constructor parameter is validated at init time
  - All verdicts are written before the method returns
  - Double-REJECTED payloads escalate automatically
"""

from __future__ import annotations

import hashlib
import json
import os
from abc import ABCMeta
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import structlog
from pydantic import ValidationError
from sqlalchemy import bindparam, column, select, table, text

from src.agents.base import HALLUCINATION_THRESHOLD, BaseAgent
from src.db.factory import CloudSqlFactory
from src.db.types import TEXT_ARRAY, TIMESTAMP, json_value, typed_text
from src.exceptions import AuditWriteError
from src.semantic_layer.models import (
    CynefinDomain,
    EpistemicState,
    GuardianVerdict,
    ReviewStatus,
    SensitivityResult,
)
from src.semantic_layer.verdict import GOVERNANCE_APPROVE
from src.semantic_layer.verdict import claim as verdict_claim
from src.semantic_layer.verdict import sign as verdict_sign

log = structlog.get_logger(__name__)

# ── Policy rules ──────────────────────────────────────────────────────────────

_POLICY_RULES = {
    "PR-001": "No cycle detection bypass: DAG acyclicity must hold before any edge is approved.",
    "PR-002": (
        "Grounding risk: prose whose numbers or causal wording the kernel outcome it "
        "reports does not back (score > 0.7) cannot be APPROVED."
    ),
    "PR-003": "Chaotic domain mandatory escalation: CHAOTIC inputs must route to human.",
    "PR-004": "Double-rejection escalation: same payload rejected twice triggers REQUIRES_ESCALATION.",
    "PR-005": "Cycle count limit: cycle_count >= 5 requires REQUIRES_ESCALATION.",
    "PR-006": "DISORDER domain: no verdict can be APPROVED in DISORDER domain.",
    "PR-007": "Sensitivity analysis: edges without sensitivity_result blocked from APPROVED.",
    "PR-008": "Drift-triggered actions: Any action initiated by drift detection must include full evidence payload and cannot modify semantic layer without re-validation workflow.",
    "PR-009": "Data contract enforcement: Agent actions consuming data products with active contract violations must be blocked or warned. Violations are surfaced for human review.",
}


#: Where a payload names the object a verdict would authorise. An agent proposing a
#: draft puts the object's own id here; the API passes the path's object id. A payload
#: with none gets a verdict that records what was checked but authorises no write.
_SUBJECT_KEYS = ("subject_id", "object_id")


def _subject_of(payload: dict[str, Any]) -> str | None:
    for key in _SUBJECT_KEYS:
        value = payload.get(key)
        if value:
            return str(value)
    return None


# ── PR-007: what an edge's sensitivity result is ──────────────────────────────
#
# CausalEdge has no sensitivity field. The platform stores a SensitivityResult
# (E-value / Rosenbaum) on an Assumption (001_initial.sql,
# assumptions.sensitivity_result), and an edge names the assumptions it rests on in
# assumption_refs. An edge therefore has a sensitivity result when at least one
# assumption it references carries a valid SensitivityResult and has not been
# REJECTED in review. It is read from the database, never from the payload being
# checked: a caller that could attach its own result would choose it.

_ASSUMPTIONS = table("assumptions", column("id"), column("review_status"),
                     column("sensitivity_result"))


@dataclass(frozen=True)
class EdgeSensitivity:
    """The sensitivity results stored for the edge a check would approve."""

    edge_id: str
    assumption_count: int
    results: list[SensitivityResult] = field(default_factory=list)


async def edge_sensitivity(payload: dict[str, Any]) -> EdgeSensitivity | None:
    """The stored sensitivity results of the edge an approval check names, if any.

    None when the check is not for GOVERNANCE_APPROVE, names no subject, or its
    subject is not a stored edge: PR-007 judges approving an edge and nothing else.
    """
    if payload.get("action_type") != GOVERNANCE_APPROVE:
        return None
    subject = _subject_of(payload)
    if subject is None:
        return None
    try:
        subject = str(UUID(subject))
    except ValueError:
        return None

    async with CloudSqlFactory.get_session_factory()() as session:
        edge = await session.execute(
            text("SELECT assumption_refs FROM causal_edges WHERE id = :eid"),
            {"eid": subject},
        )
        found = edge.one_or_none()
        if found is None:
            return None
        refs = [str(r) for r in (json_value(found[0], []) or [])]
        results: list[SensitivityResult] = []
        if refs:
            rows = await session.execute(
                select(
                    _ASSUMPTIONS.c.review_status, _ASSUMPTIONS.c.sensitivity_result
                ).where(_ASSUMPTIONS.c.id.in_(bindparam("ids", expanding=True))),
                {"ids": refs},
            )
            for status, stored in rows.all():
                if stored is None or str(status) == ReviewStatus.REJECTED.value:
                    continue
                try:
                    results.append(SensitivityResult.model_validate(json_value(stored)))
                except (ValidationError, ValueError, TypeError):
                    continue
    return EdgeSensitivity(edge_id=subject, assumption_count=len(refs), results=results)


class GuardianCheckOutput:
    """
    Typed output of a Guardian check.

    Attributes:
        verdict:          APPROVED | REJECTED | REQUIRES_ESCALATION
        reason:           Human-readable explanation
        policy_rules_cited: Which policy rules fired
        audit_entry_id:   ID of the written audit_log entry
        session_id:       Session from the input EpistemicState
    """

    __slots__ = (
        "verdict",
        "reason",
        "policy_rules_cited",
        "audit_entry_id",
        "session_id",
    )

    def __init__(
        self,
        verdict: GuardianVerdict,
        reason: str,
        policy_rules_cited: list[str],
        audit_entry_id: UUID,
        session_id: str | None = None,
    ) -> None:
        self.verdict = verdict
        self.reason = reason
        self.policy_rules_cited = policy_rules_cited
        self.audit_entry_id = audit_entry_id
        self.session_id = session_id

    def __repr__(self) -> str:
        return (
            f"GuardianCheckOutput(verdict={self.verdict}, "
            f"audit_entry_id={self.audit_entry_id})"
        )


# ── Bypass-prevention metaclass ───────────────────────────────────────────────


class _BypassGuard(ABCMeta):
    """
    Metaclass that prevents GovernanceAgent from being constructed with any
    parameter whose name contains 'bypass'.

    This is a defence-in-depth measure alongside the __init__ check.
    """

    def __call__(cls, *args: Any, **kwargs: Any) -> Any:
        for key in kwargs:
            if "bypass" in key.lower():
                raise TypeError(
                    f"GovernanceAgent: parameter '{key}' is forbidden. "
                    "The Guardian cannot be instantiated with a bypass mode."
                )
        return super().__call__(*args, **kwargs)


# ── GovernanceAgent ───────────────────────────────────────────────────────────


class GovernanceAgent(BaseAgent, metaclass=_BypassGuard):
    """
    The Guardian: cross-cutting policy enforcement agent.

    Instantiation:
        agent = GovernanceAgent(agent_id="governance-agent-001")

    The GovernanceAgent MUST NOT be instantiated with bypass_mode or any
    equivalent parameter.  The metaclass enforces this.

    All check() calls write to audit_log before returning.
    """

    def __init__(self, agent_id: str = "governance-agent") -> None:
        # Explicit guard: ensure no bypass env var is set
        if os.getenv("GUARDIAN_BYPASS_MODE", "").lower() in ("1", "true", "yes"):
            raise RuntimeError(
                "GUARDIAN_BYPASS_MODE environment variable is set. "
                "The Guardian cannot run in bypass mode. "
                "Unset GUARDIAN_BYPASS_MODE and restart."
            )
        super().__init__(agent_id=agent_id)

        # In-memory rejection tracker for double-rejection escalation (PR-004).
        # Key: payload_hash → rejection count in this process lifetime.
        self._rejection_counts: dict[str, int] = {}

    # ── process() — BaseAgent contract ───────────────────────────────────────

    async def process(self, state: EpistemicState) -> EpistemicState:
        """
        Run a governance check on the current EpistemicState and return an
        updated state with the guardian_verdict populated.

        Used by the LangGraph GuardianCheck node.
        """
        state = self._check_circuit_breaker(state)

        action_payload: dict[str, Any] = {
            "proposed_edges": [e.model_dump() for e in state.proposed_edges],
            "cynefin_domain": state.cynefin_domain,
            "hallucination_risk_score": state.hallucination_risk_score,
            "cycle_count": state.cycle_count,
            "active_assumptions": [a.model_dump() for a in state.active_assumptions],
        }

        result = await self.check(action_payload, state)

        state = state.model_copy(
            update={
                "guardian_verdict": result.verdict,
                "policy_rules_cited": result.policy_rules_cited,
            }
        )
        state = self._add_reasoning_step(
            state,
            node="GuardianCheck",
            action="GUARDIAN_CHECK",
            inputs=action_payload,
            outputs={
                "verdict": result.verdict,
                "audit_entry_id": str(result.audit_entry_id),
                "reason": result.reason,
            },
        )

        self._log.info(
            "governance.process.complete",
            session_id=state.session_id,
            verdict=result.verdict,
            audit_entry_id=str(result.audit_entry_id),
            rules=result.policy_rules_cited,
        )
        return state

    # ── check() — primary Guardian API ───────────────────────────────────────

    async def check(
        self,
        action_payload: dict[str, Any],
        state: EpistemicState,
    ) -> GuardianCheckOutput:
        """
        Evaluate an action payload against platform policy rules.

        Steps:
          1. Compute payload hash (for deduplication and audit linkage)
          2. Run all policy checks
          3. Write verdict to audit_log (BEFORE returning — immutability contract)
          4. Return GuardianCheckOutput

        Raises nothing — errors are captured into a REJECTED verdict with
        an internal_error reason.
        """
        payload_hash = self._hash_payload(action_payload)
        rules_fired: list[str] = []
        verdict = GuardianVerdict.APPROVED
        reason = "All policy checks passed."

        try:
            # Inside the try: if the edge cannot be read, the check fails closed.
            sensitivity = await edge_sensitivity(action_payload)
            verdict, reason, rules_fired = self._evaluate_policies(
                action_payload, state, payload_hash, sensitivity
            )
        except Exception as exc:
            verdict = GuardianVerdict.REJECTED
            reason = f"Internal policy evaluation error: {exc}"
            rules_fired = []
            self._log.error(
                "governance.policy_eval.error",
                error=str(exc),
                session_id=state.session_id,
            )

        # Write audit entry BEFORE returning — this is the immutability contract.
        # The subject is what the verdict will be allowed to authorise, and nothing
        # else: a payload that names no subject yields a verdict that can be read
        # but cannot be spent.
        audit_entry_id = await self._write_audit_entry(
            agent_id=self.agent_id,
            action_type=action_payload.get("action_type", "GUARDIAN_CHECK"),
            payload_hash=payload_hash,
            verdict=verdict,
            policy_rules_cited=rules_fired,
            session_id=state.session_id,
            subject_id=_subject_of(action_payload),
        )

        self._log.info(
            "governance.check.verdict",
            verdict=verdict,
            reason=reason,
            rules_fired=rules_fired,
            audit_entry_id=str(audit_entry_id),
            session_id=state.session_id,
            payload_hash=payload_hash[:16] + "...",
        )

        return GuardianCheckOutput(
            verdict=verdict,
            reason=reason,
            policy_rules_cited=rules_fired,
            audit_entry_id=audit_entry_id,
            session_id=state.session_id,
        )

    # ── record_decision() — what a person decided, and why ───────────────────

    async def record_decision(
        self,
        *,
        action_type: str,
        subject_id: str,
        verdict: GuardianVerdict,
        notes: str | None,
        actor_id: str,
        session_id: str | None = None,
    ) -> UUID:
        """Record a human decision in the audit trail, with the words they gave.

        This is not a ruling and does not pretend to be one: no policy runs, and the
        row is written *unsigned*, so it can be read back as history and can never be
        spent as authority for a write. It exists because the API has always accepted
        approval notes and a mandatory rejection reason and then dropped both -- the
        reason reached audit_log only inside the payload hash, where nothing can read
        it.
        """
        payload = {
            "action_type": action_type,
            "subject_id": subject_id,
            "actor_id": actor_id,
            "verdict": str(verdict),
        }
        return await self._write_audit_entry(
            agent_id=f"human:{actor_id}",
            action_type=action_type,
            payload_hash=self._hash_payload(payload),
            verdict=verdict,
            policy_rules_cited=[],
            session_id=session_id,
            subject_id=subject_id,
            notes=notes,
            authority=False,
        )

    # ── Policy evaluation ─────────────────────────────────────────────────────

    def _evaluate_policies(
        self,
        payload: dict[str, Any],
        state: EpistemicState,
        payload_hash: str,
        sensitivity: EdgeSensitivity | None = None,
    ) -> tuple[GuardianVerdict, str, list[str]]:
        """
        Run all policy rules in priority order.

        ``sensitivity`` is what :func:`edge_sensitivity` read for the edge an
        approval check names; None when the check is not approving an edge.

        Returns (verdict, reason, rules_fired).
        Stops at the first REJECTED or REQUIRES_ESCALATION verdict.
        """
        rules_fired: list[str] = []

        # PR-005: Circuit breaker (max cycles)
        if state.cycle_count >= 5:
            rules_fired.append("PR-005")
            return (
                GuardianVerdict.REQUIRES_ESCALATION,
                f"Cycle count {state.cycle_count} has reached maximum (5). "
                "Mandatory human escalation required.",
                rules_fired,
            )

        # PR-003: Chaotic domain mandatory escalation
        if state.cynefin_domain == CynefinDomain.CHAOTIC:
            rules_fired.append("PR-003")
            return (
                GuardianVerdict.REQUIRES_ESCALATION,
                "CHAOTIC domain: must route to human incident commander. "
                "No automated approval permitted.",
                rules_fired,
            )

        # PR-006: DISORDER domain — cannot approve
        if state.cynefin_domain == CynefinDomain.DISORDER:
            rules_fired.append("PR-006")
            return (
                GuardianVerdict.REJECTED,
                "DISORDER domain: domain must be classified before any approval.",
                rules_fired,
            )

        # PR-002: Grounding risk (src.agents.grounding_risk computes the score)
        if state.hallucination_risk_score > HALLUCINATION_THRESHOLD:
            rules_fired.append("PR-002")
            return (
                GuardianVerdict.REJECTED,
                f"Grounding risk {state.hallucination_risk_score:.3f} "
                f"exceeds threshold {HALLUCINATION_THRESHOLD}: the narrative says more "
                "than the outcome it reports.",
                rules_fired,
            )

        # PR-001: Cycle detection (DAG acyclicity)
        proposed_edges = payload.get("proposed_edges", [])
        if proposed_edges and self._has_cycle(proposed_edges):
            rules_fired.append("PR-001")
            return (
                GuardianVerdict.REJECTED,
                "DAG cycle detected in proposed edges. "
                "Causal graph must be acyclic. Rejected.",
                rules_fired,
            )

        # PR-007: an edge is not approved without a sensitivity result
        if sensitivity is not None and not sensitivity.results:
            rules_fired.append("PR-007")
            return (
                GuardianVerdict.REJECTED,
                f"Edge {sensitivity.edge_id} has no sensitivity result: none of the "
                f"{sensitivity.assumption_count} assumption(s) it rests on carries one "
                "(or each that does was rejected in review). Record a sensitivity "
                "analysis (E-value / Rosenbaum) on an assumption the edge references "
                "before approving it.",
                rules_fired,
            )

        # PR-004: Double-rejection escalation
        rejection_count = self._rejection_counts.get(payload_hash, 0)
        if rejection_count >= 2:
            rules_fired.append("PR-004")
            return (
                GuardianVerdict.REQUIRES_ESCALATION,
                "This payload has been REJECTED twice. "
                "Escalating to human reviewer per PR-004.",
                rules_fired,
            )

        # PR-008: Drift-triggered actions must include evidence
        # and cannot autonomously modify the semantic layer.
        action_type = payload.get("action_type", "")
        if action_type.startswith("DRIFT_") or action_type == "DRIFT_CHECK":
            if payload.get("proposed_edges") or payload.get("semantic_layer_write"):
                rules_fired.append("PR-008")
                return (
                    GuardianVerdict.REJECTED,
                    "Drift-triggered actions cannot modify the semantic layer "
                    "without human review. Initiate re-validation workflow instead.",
                    rules_fired,
                )
            # Drift alerts that are purely informational (no write intent)
            # are allowed through, but flagged for audit.
            self._log.info(
                "governance.drift_alert_processed",
                action_type=action_type,
                session_id=state.session_id,
            )

        # PR-009: Data contract enforcement — block agent actions
        # when active contract violations exist for consumed data products.
        if action_type == "DATA_ACCESS_WITH_CONTRACT":
            contract_violations = payload.get("contract_violations", [])
            if contract_violations:
                violation_severities = {
                    v.get("severity", "MEDIUM") for v in contract_violations
                }
                if "CRITICAL" in violation_severities or "HIGH" in violation_severities:
                    rules_fired.append("PR-009")
                    return (
                        GuardianVerdict.REJECTED,
                        f"Data contract violation detected ({len(contract_violations)} violations). "
                        "Agent action blocked per PR-009. Review data product freshness.",
                        rules_fired,
                    )
                # MEDIUM/LOW violations — warn but allow
                self._log.warning(
                    "governance.contract_violation_warning",
                    violation_count=len(contract_violations),
                    severities=list(violation_severities),
                    session_id=state.session_id,
                )
                rules_fired.append("PR-009")

        # All checks passed
        return GuardianVerdict.APPROVED, "All policy checks passed.", rules_fired

    def _has_cycle(self, proposed_edges: list[dict[str, Any]]) -> bool:
        """
        Detect cycles in a proposed edge set using DFS.

        Each edge is expected to have 'source' and 'target' keys, each with an
        'id' sub-key.  Missing keys are treated as no-edge (safe default).
        """
        adjacency: dict[str, list[str]] = {}
        for edge in proposed_edges:
            try:
                source = str(edge["source"]["id"])
                target = str(edge["target"]["id"])
                adjacency.setdefault(source, []).append(target)
            except (KeyError, TypeError):
                continue

        visited: set[str] = set()
        rec_stack: set[str] = set()

        def dfs(node: str) -> bool:
            visited.add(node)
            rec_stack.add(node)
            for neighbour in adjacency.get(node, []):
                if neighbour not in visited:
                    if dfs(neighbour):
                        return True
                elif neighbour in rec_stack:
                    return True
            rec_stack.discard(node)
            return False

        for node in list(adjacency.keys()):
            if node not in visited and dfs(node):
                return True
        return False

    # ── Audit log write ───────────────────────────────────────────────────────

    async def _write_audit_entry(
        self,
        agent_id: str,
        action_type: str,
        payload_hash: str,
        verdict: GuardianVerdict,
        policy_rules_cited: list[str],
        session_id: str | None,
        subject_id: str | None = None,
        notes: str | None = None,
        authority: bool = True,
    ) -> UUID:
        """
        Append an immutable entry to audit_log.

        This is the ONLY method that writes to audit_log in this class.
        It uses an INSERT — never UPDATE or DELETE.

        Raises AuditWriteError if the insert fails (which is a P0 incident).
        """
        entry_id = uuid4()
        verdict_value = verdict.value if hasattr(verdict, "value") else str(verdict)
        now = datetime.now(UTC)
        # The claim signs the ISO text; the column is bound as the datetime it came
        # from. The registry reads it back through timestamp_text(), which renders a
        # TIMESTAMPTZ as this same text, so the signature verifies on either store.
        created_at = now.isoformat()
        # Signed here and nowhere else. A verdict's authority comes from this
        # signature, so the one method that writes audit_log is the one method that
        # can confer it -- an INSERT from anywhere else produces history, not power.
        #
        # `authority=False` writes the row unsigned on purpose. A record of a human
        # decision belongs in the trail, but it is not a ruling and must never be
        # spendable as one: unsigned is exactly how the registry reads "history, not
        # authority", so the record is safe by the same rule that stops a forgery.
        signature = (
            verdict_sign(
                verdict_claim(
                    verdict_id=entry_id,
                    subject_id=subject_id,
                    payload_hash=payload_hash,
                    verdict=verdict_value,
                    action_type=action_type,
                    agent_id=agent_id,
                    created_at=created_at,
                )
            )
            if authority
            else None
        )
        try:
            async with CloudSqlFactory.get_session_factory()() as session:
                await session.execute(
                    typed_text(
                        "INSERT INTO audit_log "
                        "(id, agent_id, action_type, payload_hash, guardian_verdict, "
                        " policy_rules_cited, session_id, created_at, subject_id, signature, "
                        " notes) "
                        "VALUES "
                        "(:id, :agent_id, :action_type, :payload_hash, :verdict, "
                        " :rules, :session_id, :created_at, :subject_id, :signature, :notes)",
                        rules=TEXT_ARRAY,
                        created_at=TIMESTAMP,
                    ),
                    {
                        "id": str(entry_id),
                        "agent_id": agent_id,
                        "action_type": action_type,
                        "payload_hash": payload_hash,
                        "verdict": verdict_value,
                        "rules": list(policy_rules_cited),
                        "session_id": session_id,
                        "created_at": now,
                        "subject_id": subject_id,
                        "signature": signature,
                        "notes": notes,
                    },
                )
                await session.commit()
        except Exception as exc:
            self._log.critical(
                "governance.audit_write.FAILED",
                error=str(exc),
                payload_hash=payload_hash,
                verdict=str(verdict),
            )
            raise AuditWriteError(
                f"Critical: audit_log write failed for verdict {verdict}: {exc}"
            ) from exc

        # Track rejections for PR-004
        if verdict == GuardianVerdict.REJECTED:
            self._rejection_counts[payload_hash] = (
                self._rejection_counts.get(payload_hash, 0) + 1
            )

        return entry_id

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _hash_payload(payload: dict[str, Any]) -> str:
        """Compute deterministic SHA-256 hash of an action payload."""
        canonical = json.dumps(payload, sort_keys=True, default=str)
        return hashlib.sha256(canonical.encode()).hexdigest()

    # ── execute_skill override ────────────────────────────────────────────────

    async def _execute_skill(
        self,
        skill_name: str,
        inputs: dict[str, Any],
        state: EpistemicState,
    ) -> dict[str, Any]:
        """Route governance-specific skill calls."""
        if skill_name == "guardian_check":
            result = await self.check(inputs, state)
            return {
                "verdict": result.verdict,
                "reason": result.reason,
                "audit_entry_id": str(result.audit_entry_id),
                "policy_rules_cited": result.policy_rules_cited,
            }
        if skill_name == "audit_write":
            # The verdict is not the caller's to choose. This used to read
            # `GuardianVerdict(inputs.get("verdict", "APPROVED"))`, so an agent
            # asked to submit itself for review could instead write down that it
            # had passed -- the one thing a policy engine exists to prevent.
            #
            # The row is still written, because a request to record something is
            # worth recording. It is written as a note, unsigned, with no verdict
            # of its own: PENDING is what "nobody has ruled on this" means, and an
            # unsigned row authorises nothing. An agent that wants a ruling calls
            # guardian_check, which runs the policies.
            entry_id = await self._write_audit_entry(
                agent_id=inputs.get("agent_id", self.agent_id),
                action_type=inputs.get("action_type", "MANUAL_AUDIT"),
                payload_hash=inputs.get("payload_hash", self._hash_payload(inputs)),
                verdict=GuardianVerdict.PENDING,
                policy_rules_cited=[],
                session_id=inputs.get("session_id"),
                notes=inputs.get("notes"),
                authority=False,
            )
            return {
                "audit_entry_id": str(entry_id),
                "verdict": str(GuardianVerdict.PENDING),
                "note": (
                    "Recorded, not ruled on. Call guardian_check for a verdict; "
                    "this skill cannot issue one."
                ),
            }

        return await super()._execute_skill(skill_name, inputs, state)
