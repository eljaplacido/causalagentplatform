# Provider-Neutral Handoff & Orchestration Standard

> **Scope:** Standard for structured handoff between agentic providers (OpenCode Go, GitHub Copilot Pro, Claude Code, Codex, etc.) across the Unified Enterprise Causal Platform.
> **Status:** Active — 2026-04-07 (promoted from Proposed 2026-04-30)

---

## 1. Purpose

This document standardizes how agentic providers pick up work from each other without re-interpretation. Any provider can start from a structured handoff packet and immediately execute without requiring additional context gathering or architecture debates.

---

## 2. Handoff Packet Format

Every handoff between providers MUST include these fields:

| Field | Type | Required | Description |
|---|---|---|---|
| `goal` | string | Yes | Clear statement of what success looks like |
| `target_files` | string[] | Yes | Files to read/modify (absolute paths) |
| `acceptance_criteria` | string[] | Yes | Measurable conditions for completion |
| `epistemic_state_constraints` | object | Yes | Constraints on EpistemicState (see §3) |
| `verification_commands` | string[] | Yes | Commands to verify success |
| `lane_classification` | enum | Yes | `govern`, `plan`, `implement` |
| `lead_provider` | string | Yes | Provider best-suited for this work |
| `dependencies` | string[] | No | Other packets that must complete first |
| `blocked_by` | string[] | No | Items blocking this work |

### Example Handoff Packet

```json
{
  "goal": "Complete SimulationAgent skill implementations with DoWhy integration",
  "target_files": [
    "backend/src/agents/simulation.py",
    "backend/src/skills/effect_estimate.py"
  ],
  "acceptance_criteria": [
    "effect_estimate returns real ATE values from DoWhy",
    "All stub TODO markers removed",
    "Tests pass for ATE estimation with synthetic data"
  ],
  "epistemic_state_constraints": {
    "require_approved_fragment": true,
    "guardian_verdict_required": true,
    "audit_log_required": true
  },
  "verification_commands": [
    "python -m pytest backend/tests/unit/test_simulation.py -v",
    "python -m pytest backend/tests/guardian/test_guardian_bypass.py -v"
  ],
  "lane_classification": "implement",
  "lead_provider": "Cursor/Copilot",
  "dependencies": ["ADR-001-canonical-semantic-layer"],
  "blocked_by": []
}
```

---

## 3. EpistemicState Constraints

The `epistemic_state_constraints` field specifies what the receiving agent MUST preserve:

| Constraint | Meaning |
|---|---|
| `require_approved_fragment` | Only APPROVED GraphFragments usable in simulations |
| `guardian_verdict_required` | All semantic writes require Guardian APPROVED |
| `audit_log_required` | Every agent action logged to audit trail |
| `cynefin_domain_required` | Agent must set cynefinDomain before reasoning |
| `circuit_breaker_enforced` | Max 5 cycles before escalation |
| `hallucination_risk_checked` | Hallucination risk score must be computed |

---

## 4. Lane Classification

Each work packet is classified into one of three lanes:

| Lane | Purpose | Typical Lead Provider |
|---|---|---|
| **govern** | Architecture decisions, standards, ADRs, cross-repo coordination | Hermes, Claude Code |
| **plan** | Task decomposition, sequencing, acceptance criteria derivation | Claude Code, OpenCode Go |
| **implement** | Code changes, tests, skill implementations | Cursor, Copilot, OpenCode Go |

**Rules:**
- `govern` tasks complete before `plan`
- `plan` tasks complete before `implement`
- No `implement` work begins until `govern` + `plan` are green

---

## 5. Provider Capability Mapping

Providers are mapped by capability, NOT by model name:

| Capability | Primary Provider | Fallback |
|---|---|---|
| Architecture reasoning (govern lane) | Claude Code, Hermes | OpenCode Go |
| Task decomposition (plan lane) | OpenCode Go, Claude Code | — |
| Bounded code changes (implement lane) | Cursor, Copilot Pro | OpenCode Go |
| Tricky integration review | Claude Code | — |
| Test-first implementation | Cursor, Copilot Pro | OpenCode Go |
| Parallel scaffolding | OpenCode Go | — |

**Selection logic:**
1. Match capability to primary provider
2. If primary unavailable, use fallback
3. If neither available, escalate to human

---

## 6. Verification Commands

Every handoff packet MUST include verification commands. These are run by the receiving provider to confirm success:

```bash
# Example verification commands

# Run unit tests for modified agent
python -m pytest backend/tests/unit/test_simulation.py -v

# Run Guardian bypass tests
python -m pytest backend/tests/guardian/test_guardian_bypass.py -v

# Run integration tests
python -m pytest backend/tests/integration/ -v

# Type check
cd backend && python -m mypy src/agents/simulation.py

# Lint
cd backend && python -m ruff check src/agents/simulation.py
```

---

## 7. Cross-Repo Coordination

When work spans multiple repos, the handoff packet MUST include:

1. **Repo-specific targets** — per-repo file lists
2. **Shared semantic layer reference** — which canonical spec version
3. **Migration notes** — what must change vs. what must not (from ADR-001)
4. **Verification per repo** — separate commands for each repo

---

## 8. Packet Lifecycle

```
[CREATED] → [ACCEPTED] → [IN_PROGRESS] → [VERIFIED] → [DONE]
    │           │             │              │
    └───────────┴─────────────┴──────────────┘
         Rejected or Blocked
```

- **CREATED**: Packet written by planning agent
- **ACCEPTED**: Receiving provider acknowledges
- **IN_PROGRESS**: Work underway
- **VERIFIED**: Verification commands passed
- **DONE**: Handoff complete, next packet can start

---

## 9. Anti-Patterns to Avoid

| Anti-Pattern | Why It Fails |
|---|---|
| "Figure it out" — no goal stated | Provider spends time interpreting instead of executing |
| Missing verification commands | No way to confirm success |
| Model-name-based routing | Breaks when model availability changes |
| Skipping govern before implement | Architecture debates reopen mid-implementation |
| No epistemic_state_constraints | Agent may violate Guardian or audit rules |

---

## 10. Integration with Existing Docs

This standard aligns with:
- **docs/AGENTS.md** — Agent contract, EpistemicState, Guardian requirements
- **docs/DATA_LAYER.md** — Semantic layer object model, versioning
- **docs/CURRENT_STAGE.md** — Phase gates and dependencies
- **docs/ADR-001-canonical-semantic-layer.md** — Unified semantic spec
- **docs/ORCHESTRATION.md** — Lane definitions and provider routing

---

## 11. Adoption Checklist

- [ ] All planning outputs use handoff packet format
- [ ] Every packet includes verification commands
- [ ] Provider selection uses capability mapping, not model names
- [ ] Lane classification enforced (govern → plan → implement)
- [ ] EpistemicState constraints specified for all implement tasks
- [ ] Cross-repo packets include migration notes from ADRs