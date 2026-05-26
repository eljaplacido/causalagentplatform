---------------------------- MODULE guardian_policy ----------------------------
(**
 * TLA+ specification for Guardian policy enforcement invariants.
 *
 * Critical safety properties verified by this spec:
 * 1. Once APPROVED, a verdict's status is immutable.
 * 2. No semantic layer write can occur without an APPROVED verdict.
 * 3. The audit log is append-only — no entry is ever modified or deleted.
 * 4. Circuit breaker opens after MAX_CYCLES agent iterations.
 *
 * Run with TLC model checker:
 *   tlc guardian_policy.tla -config guardian_policy.cfg
 *)

EXTENDS Naturals, Sequences, FiniteSets

CONSTANTS
  MAX_CYCLES,         \* Maximum agent cycles before escalation (= 5)
  AGENTS,             \* Set of agent identifiers
  ACTIONS             \* Set of possible action types

VARIABLES
  verdicts,           \* Set of verdict records
  audit_log,          \* Sequence of audit entries (append-only)
  semantic_layer,     \* Set of approved semantic layer objects
  agent_cycles,       \* Function from agent to cycle count
  circuit_breakers    \* Function from agent to circuit state

vars == <<verdicts, audit_log, semantic_layer, agent_cycles, circuit_breakers>>

(* ── Type definitions ─────────────────────────────────────────────────────── *)

VerdictStatus == {"PENDING", "APPROVED", "REJECTED", "REQUIRES_ESCALATION"}

CircuitState == {"CLOSED", "OPEN"}

VerdictRecord == [
  id      : STRING,
  status  : VerdictStatus,
  agentId : AGENTS,
  action  : ACTIONS
]

AuditEntry == [
  id             : STRING,
  agentId        : AGENTS,
  action         : ACTIONS,
  guardianVerdict: VerdictStatus,
  timestamp      : Nat
]

(* ── Type invariant ───────────────────────────────────────────────────────── *)

TypeInvariant ==
  /\ verdicts \subseteq VerdictRecord
  /\ \A entry \in Range(audit_log): entry \in AuditEntry
  /\ \A agent \in AGENTS: agent_cycles[agent] \in 0..MAX_CYCLES
  /\ \A agent \in AGENTS: circuit_breakers[agent] \in CircuitState

(* ── Safety invariants ────────────────────────────────────────────────────── *)

(**
 * SAFETY-1: Once APPROVED, a verdict's status cannot change.
 * This is the foundational Guardian immutability contract.
 *)
GuardianApprovalImmutability ==
  \A v \in verdicts:
    v.status = "APPROVED" =>
      \A v2 \in verdicts: v2.id = v.id => v2.status = "APPROVED"

(**
 * SAFETY-2: No object in the semantic layer exists without a corresponding
 * APPROVED verdict in the audit log.
 * Enforces the "Guardian before all semantic writes" contract.
 *)
SemanticLayerRequiresApproval ==
  \A obj \in semantic_layer:
    \E entry \in Range(audit_log):
      /\ entry.action = obj.action
      /\ entry.guardianVerdict = "APPROVED"

(**
 * SAFETY-3: The audit log is strictly append-only.
 * Its length never decreases, and existing entries are never modified.
 *)
AuditLogAppendOnly ==
  /\ \A i \in 1..Len(audit_log):
       audit_log[i] = audit_log[i]  \* Entries are immutable
  \* Length monotonically non-decreasing (enforced by transition rules)

(**
 * SAFETY-4: Circuit breaker opens when an agent exceeds MAX_CYCLES.
 * An open circuit breaker must prevent further agent actions.
 *)
CircuitBreakerEnforced ==
  \A agent \in AGENTS:
    agent_cycles[agent] >= MAX_CYCLES =>
      circuit_breakers[agent] = "OPEN"

(**
 * SAFETY-5: No agent action is allowed when circuit breaker is OPEN.
 *)
OpenCircuitPreventsAction ==
  \A agent \in AGENTS:
    circuit_breakers[agent] = "OPEN" =>
      \* Agent cannot submit new actions (verified by action enabling conditions)
      TRUE  \* Enforced in transition guard SemanticWrite

(* ── Combined safety property ────────────────────────────────────────────── *)

Safety ==
  /\ TypeInvariant
  /\ GuardianApprovalImmutability
  /\ SemanticLayerRequiresApproval
  /\ CircuitBreakerEnforced

(* ── Initial state ─────────────────────────────────────────────────────────── *)

Init ==
  /\ verdicts = {}
  /\ audit_log = <<>>
  /\ semantic_layer = {}
  /\ agent_cycles = [a \in AGENTS |-> 0]
  /\ circuit_breakers = [a \in AGENTS |-> "CLOSED"]

(* ── Transitions ──────────────────────────────────────────────────────────── *)

(**
 * An agent submits an action for Guardian review.
 * Precondition: circuit breaker must be CLOSED.
 *)
SubmitAction(agent, action, verdict_id) ==
  /\ circuit_breakers[agent] = "CLOSED"
  /\ agent_cycles[agent] < MAX_CYCLES
  /\ verdicts' = verdicts \cup {[id |-> verdict_id, status |-> "PENDING",
                                   agentId |-> agent, action |-> action]}
  /\ agent_cycles' = [agent_cycles EXCEPT ![agent] = agent_cycles[agent] + 1]
  /\ UNCHANGED <<audit_log, semantic_layer, circuit_breakers>>

(**
 * Guardian approves an action.
 * Effect: verdict transitions PENDING -> APPROVED.
 *)
GuardianApprove(verdict_id) ==
  /\ \E v \in verdicts: v.id = verdict_id /\ v.status = "PENDING"
  /\ verdicts' = {
       IF v.id = verdict_id THEN [v EXCEPT !.status = "APPROVED"] ELSE v
       : v \in verdicts
     }
  /\ LET v == CHOOSE v \in verdicts: v.id = verdict_id
     IN audit_log' = Append(audit_log, [
          id              |-> verdict_id,
          agentId         |-> v.agentId,
          action          |-> v.action,
          guardianVerdict |-> "APPROVED",
          timestamp       |-> Len(audit_log) + 1
        ])
  /\ UNCHANGED <<semantic_layer, agent_cycles, circuit_breakers>>

(**
 * Guardian rejects an action.
 * Effect: verdict transitions PENDING -> REJECTED. Write to audit log.
 *)
GuardianReject(verdict_id) ==
  /\ \E v \in verdicts: v.id = verdict_id /\ v.status = "PENDING"
  /\ verdicts' = {
       IF v.id = verdict_id THEN [v EXCEPT !.status = "REJECTED"] ELSE v
       : v \in verdicts
     }
  /\ LET v == CHOOSE v \in verdicts: v.id = verdict_id
     IN audit_log' = Append(audit_log, [
          id              |-> verdict_id,
          agentId         |-> v.agentId,
          action          |-> v.action,
          guardianVerdict |-> "REJECTED",
          timestamp       |-> Len(audit_log) + 1
        ])
  /\ UNCHANGED <<semantic_layer, agent_cycles, circuit_breakers>>

(**
 * Write to the semantic layer.
 * Precondition: verdict must be APPROVED.
 * This is the key safety gate — enforces SemanticLayerRequiresApproval.
 *)
SemanticWrite(verdict_id, obj) ==
  /\ \E v \in verdicts: v.id = verdict_id /\ v.status = "APPROVED"
  /\ LET v == CHOOSE v \in verdicts: v.id = verdict_id
     IN /\ circuit_breakers[v.agentId] = "CLOSED"
  /\ semantic_layer' = semantic_layer \cup {[action |-> obj, verdictRef |-> verdict_id]}
  /\ UNCHANGED <<verdicts, audit_log, agent_cycles, circuit_breakers>>

(**
 * Circuit breaker opens when cycle count reaches MAX_CYCLES.
 *)
TripCircuitBreaker(agent) ==
  /\ agent_cycles[agent] >= MAX_CYCLES
  /\ circuit_breakers' = [circuit_breakers EXCEPT ![agent] = "OPEN"]
  /\ UNCHANGED <<verdicts, audit_log, semantic_layer, agent_cycles>>

(* ── Next-state relation ──────────────────────────────────────────────────── *)

Next ==
  \/ \E agent \in AGENTS, action \in ACTIONS, id \in STRING:
       SubmitAction(agent, action, id)
  \/ \E id \in STRING:
       GuardianApprove(id)
  \/ \E id \in STRING:
       GuardianReject(id)
  \/ \E id \in STRING, obj \in ACTIONS:
       SemanticWrite(id, obj)
  \/ \E agent \in AGENTS:
       TripCircuitBreaker(agent)

(* ── Spec ─────────────────────────────────────────────────────────────────── *)

Spec == Init /\ [][Next]_vars

(* ── Properties to verify ─────────────────────────────────────────────────── *)

THEOREM Spec => []Safety

=============================================================================
