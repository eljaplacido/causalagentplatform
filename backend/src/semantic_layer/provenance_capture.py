"""
The provenance each running flow records (ADR-002 Part A, as read against the code).

ADR-002 names three sources of provenance: LLM calls (the capture hook in
src/agents/llm_factory.py), agents, and the Guardian. What runs today:

  * **Session runs** (POST /api/v1/session/run and /incident): the agent, the
    run, one activity per reasoning step the run added, the input and the output
    state -- `session_run_records`.
  * **Guardian checks** (GovernanceAgent.check, reached by POST
    /api/governance/check, approvals and rejections, and the GuardianCheck node of
    every session): one activity citing the audit_log row, written in the same
    transaction as that row -- `guardian_check_record`.
  * **Decisions** (POST /api/v1/decisions): the data, the graph, the analysis and
    the sealed ledger entry, under session ``decision:<entry_digest>`` --
    `decision_records`.

LLM invocations: the hook writes them, but no runtime path calls get_llm
(register llm-routing-no-callers), so no flow produces one.

What is recorded is identifiers and digests, not content: the description a user
typed, the rows of a dataset and a reasoning step's inputs are hashed, so the
trace can be checked against the objects without copying them into it.
"""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any

from src.semantic_layer.provenance import ProvenanceRecord, ProvenanceRecordType

if TYPE_CHECKING:
    from uuid import UUID

    from src.semantic_layer.models import EpistemicState

#: Session id prefix a decision's provenance is recorded under.
DECISION_SESSION_PREFIX = "decision:"


def digest(value: Any) -> str:
    """SHA-256 of the canonical JSON of `value`."""
    canonical = json.dumps(value, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _guardian_ref(state: EpistemicState) -> UUID | None:
    """The audit_log id of the last Guardian check the state records, if any."""
    from uuid import UUID

    for step in reversed(state.reasoning_steps):
        ref = step.outputs.get("audit_entry_id") if step.node == "GuardianCheck" else None
        if ref:
            try:
                return UUID(str(ref))
            except ValueError:
                return None
    return None


def session_run_records(
    initial: EpistemicState,
    final: EpistemicState,
    *,
    route: str,
    user_id: str | None,
    resumed: bool,
) -> list[ProvenanceRecord]:
    """The PROV records of one session run, in generation order."""
    sid, aid = final.session_id, final.agent_id
    agent = ProvenanceRecord(
        record_type=ProvenanceRecordType.AGENT,
        session_id=sid,
        agent_id=aid,
        label=aid,
        attributes={"acted_on_behalf_of": user_id},
    )
    source = ProvenanceRecord(
        record_type=ProvenanceRecordType.ENTITY,
        session_id=sid,
        agent_id=aid,
        label="session input",
        attributes={
            "description_sha256": digest(initial.draft_explanation),
            "resumed": resumed,
            "input_state_sha256": digest(initial.model_dump(mode="json")),
        },
    )
    run = ProvenanceRecord(
        record_type=ProvenanceRecordType.ACTIVITY,
        session_id=sid,
        agent_id=aid,
        label=route,
        used=[source.id],
        was_attributed_to=agent.id,
        attributes={"route": route, "user_id": user_id, "cycle_count": final.cycle_count},
    )
    steps = [
        ProvenanceRecord(
            record_type=ProvenanceRecordType.ACTIVITY,
            session_id=sid,
            agent_id=aid,
            label=f"{step.node}: {step.action}",
            was_attributed_to=agent.id,
            attributes={
                "part_of": str(run.id),
                "node": step.node,
                "action": step.action,
                "inputs_sha256": digest(step.inputs),
                "outputs_sha256": digest(step.outputs),
            },
            guardian_verdict_ref=_guardian_ref(final) if step.node == "GuardianCheck" else None,
        )
        for step in final.reasoning_steps[len(initial.reasoning_steps) :]
    ]
    output = ProvenanceRecord(
        record_type=ProvenanceRecordType.ENTITY,
        session_id=sid,
        agent_id=aid,
        label="session result",
        was_generated_by=run.id,
        was_derived_from=[source.id],
        was_attributed_to=agent.id,
        guardian_verdict_ref=_guardian_ref(final),
        attributes={
            "state_sha256": digest(final.model_dump(mode="json")),
            "guardian_verdict": str(final.guardian_verdict),
            "policy_rules_cited": list(final.policy_rules_cited),
            "cynefin_domain": str(final.cynefin_domain),
            "requires_human_review": final.requires_human_review,
            "recommendation_count": len(final.recommendations),
        },
    )
    return [agent, source, run, *steps, output]


def guardian_check_record(
    *,
    session_id: str,
    agent_id: str,
    audit_entry_id: UUID,
    verdict: str,
    policy_rules_cited: list[str],
    action_type: str,
    payload_hash: str,
) -> ProvenanceRecord:
    """The Guardian's own record of one check, citing its audit_log row."""
    return ProvenanceRecord(
        record_type=ProvenanceRecordType.ACTIVITY,
        session_id=session_id,
        agent_id=agent_id,
        label="Guardian check",
        guardian_verdict_ref=audit_entry_id,
        attributes={
            "action_type": action_type,
            "verdict": verdict,
            "policy_rules_cited": list(policy_rules_cited),
            "payload_sha256": payload_hash,
            "audit_entry_id": str(audit_entry_id),
        },
    )


def decision_records(
    *,
    entry_digest: str,
    sequence: int,
    verdict: str,
    decision_digest: str | None,
    outcome_kind: str,
    data: dict[str, Any],
    graph: dict[str, Any],
    analysis: dict[str, Any],
    user_id: str | None,
    agent_id: str = "decision-api",
) -> tuple[str, list[ProvenanceRecord]]:
    """The session id and PROV records of one decision."""
    sid = f"{DECISION_SESSION_PREFIX}{entry_digest}"
    agent = ProvenanceRecord(
        record_type=ProvenanceRecordType.AGENT,
        session_id=sid,
        agent_id=agent_id,
        label=agent_id,
        attributes={"acted_on_behalf_of": user_id},
    )
    data_entity = ProvenanceRecord(
        record_type=ProvenanceRecordType.ENTITY,
        session_id=sid,
        agent_id=agent_id,
        label="data",
        attributes=data,
    )
    graph_entity = ProvenanceRecord(
        record_type=ProvenanceRecordType.ENTITY,
        session_id=sid,
        agent_id=agent_id,
        label="causal graph",
        attributes=graph,
    )
    activity = ProvenanceRecord(
        record_type=ProvenanceRecordType.ACTIVITY,
        session_id=sid,
        agent_id=agent_id,
        label="analyze and decide",
        used=[data_entity.id, graph_entity.id],
        was_attributed_to=agent.id,
        model_identifier=analysis.get("causal_core_version"),
        attributes=analysis,
    )
    decided = ProvenanceRecord(
        record_type=ProvenanceRecordType.ENTITY,
        session_id=sid,
        agent_id=agent_id,
        label="ledger entry",
        was_generated_by=activity.id,
        was_derived_from=[data_entity.id, graph_entity.id],
        was_attributed_to=agent.id,
        attributes={
            "entry_digest": entry_digest,
            "sequence": sequence,
            "verdict": verdict,
            "decision_digest": decision_digest,
            "outcome_kind": outcome_kind,
        },
    )
    return sid, [agent, data_entity, graph_entity, activity, decided]
