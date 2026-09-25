# Orchestration: Multi-Agent Development & Platform Patterns

> **Canonical orchestration is the gnomon chain, not this document.** The stages a unit of work
> passes through in this repository are declared in `.gnomon/config.toml` `[chain]`:
> `coordinator` (spec, contract, register entry or pre-registration) → `implementor` (test first,
> then code) → `verifier` (runs `.gnomon/ci.sh`; cannot edit what it judges), gated `on_check`.
> Each role's tools and write scope are in `.gnomon/roles.toml`, and the working rules are in the
> root [`AGENTS.md`](../AGENTS.md) and `.gnomon/skills/`. What follows is the author's personal
> development tool stack and lane vocabulary: one way of working, not configuration this
> repository ships or depends on.
>
> **Scope:** Maps the external agentic tool stack to the platform's internal patterns. Defines work lanes, tool roles, handoff conventions, and the mirror principle connecting development methodology to platform architecture.

---

## 1. Tool Stack

### 1.1 IDE Layer

| Tool | Role | When to use |
|------|------|-------------|
| **Cursor** | Primary IDE, rules-driven focused edits | Single-agent implementation with platform contract guardrails |
| **Antigravity** | Parallel multi-agent IDE, Gemini-native | Multi-file exploration, parallel backend/frontend/docs sprints |
| **GitHub Copilot Pro** | Inline completions, agent mode, PR automation | Bounded coding tasks, issue-to-PR automation, code review |

### 1.2 CLI Agent Layer

| Tool | Role | When to use |
|------|------|-------------|
| **Claude Code** | Architecture planner, decomposer, reviewer | Complex design decisions, large refactors, impact analysis |
| **Gemini CLI** | Long-context explorer, research synthesizer | Broad codebase understanding, cross-repo research |
| **Hermes Agent** | Memory, skills, session recall, orchestration | Task routing, workflow learning, governance briefs, ADRs |
| **OpenCode + Go** | Neutral execution fabric, cheap workers | Parallel scaffolding, test generation, bounded implementation |

### 1.3 Operations Layer

| Tool | Role | When to use |
|------|------|-------------|
| **OpenFang** | Background autonomous agents | Scheduled research digests, nightly monitors, channel delivery |
| **Copilot Coding Agent** | Issue-to-PR background automation | Converting scoped GitHub issues into tested pull requests |

### 1.4 Model Routing

| Task type | Preferred provider | Fallback |
|-----------|-------------------|----------|
| Architecture reasoning | Claude (via Claude Code) | Gemini 3.1 Pro (via Antigravity) |
| Broad exploration | Gemini (via CLI or Antigravity) | Claude Code |
| Fast scaffolding | OpenCode Go (MiniMax M2.5/M2.7) | Copilot Pro agent mode |
| Reasoning tasks | OpenCode Go (GLM-5 / Kimi K2.5) | Claude Code |
| Local/private inference | Local Ollama | OpenCode Go |
| Inline completions | Copilot Pro | Cursor built-in |

---

## 2. Work Lanes

Route every incoming task into one primary lane before doing anything else.

### 2.1 Lane Map

| Lane | Primary outcome | Lead tool | Secondary tool | Avoid |
|------|----------------|-----------|----------------|-------|
| `plan` | Decision, architecture, decomposition | Claude Code | Antigravity Planning Mode | Starting implementation too early |
| `explore` | Evidence, repo understanding, synthesis | Gemini CLI | Antigravity multi-agent | Mixing with same-session code edits |
| `implement` | Bounded code or infra change | Cursor + Copilot | OpenCode + Go | Carrying huge planning context into execution |
| `govern` | ADR, release note, audit, review packet | Hermes | Claude Code | Letting governance drift into build work |
| `automate` | Recurring background workflow | OpenFang | Copilot Coding Agent | Automating unproven manual workflows |
| `review` | PR review, security scan, arch review | Copilot Pro code review | Claude Code | Reviewing without clear acceptance criteria |

### 2.2 Lane Discipline

1. Classify the task into a lane before starting work
2. Use the lead tool for that lane
3. Produce a structured handoff before transitioning to the next lane
4. Never let a single session span more than two lanes

### 2.3 Handoff Packets

Templates live in `templates/`. Every lane transition produces a structured packet:

| Transition | Template | Key fields |
|-----------|----------|------------|
| plan → implement | `planner-to-executor.md` | Goal, target files, acceptance criteria, constraints, verification |
| explore → plan | `explorer-to-planner.md` | Evidence summary, graph fragments, open questions, confidence |
| implement → govern | `governance-brief.md` | Decision record, Guardian alignment, assumption audit, risk tier |
| govern → automate | Direct OpenFang config | Schedule, triggers, delivery channels, approval gates |

---

## 3. The Mirror Principle

The external orchestration patterns are not arbitrary. They mirror the platform's own architecture:

### 3.1 Cynefin ↔ Lanes

| Cynefin Domain | Platform Behavior | Development Lane |
|---------------|-------------------|------------------|
| CLEAR | DirectAnswer → GuardianCheck → Output | `implement` — well-understood, execute directly |
| COMPLICATED | CausalAnalysis → GuardianCheck → Output | `plan` — structured analysis by experts |
| COMPLEX | CausalDiscovery → Expert Elicitation → GuardianCheck | `explore` + `plan` — emergent, probe first |
| CHAOTIC | EscalateHuman → END | `govern` — stabilize, human takes command |
| DISORDER | DisorderProtocol → re-classify | Re-classify before choosing a lane |

### 3.2 Structural Parallels

| External Pattern | Platform Pattern |
|-----------------|-----------------|
| Handoff packets between tools | `EpistemicState` transitions between agent nodes |
| Governance lane (Hermes + Claude) | `GovernanceAgent` + Guardian policy engine |
| Hermes memory and skills | Platform experience buffer + skill library |
| OpenFang scheduled agents | `DriftMonitorAgent` + continuous monitoring |
| Human approval gates in handoffs | `requires_human_review` flag + CHAOTIC escalation |
| Circuit breaker (max retries) | `MAX_CYCLES = 5` circuit breaker in agents |

### 3.3 Implication for Development

When building platform features, use the same lane discipline:
- **Planning the SimulationAgent?** Use Claude Code in the `plan` lane
- **Exploring how a new estimator fits the kernel?** Use Gemini CLI in the `explore` lane
- **Implementing the DriftMonitorAgent?** Use Cursor in the `implement` lane with a handoff packet from the planner
- **Writing an ADR for the storage decision?** Use Hermes in the `govern` lane
- **Setting up nightly test runs?** Use OpenFang in the `automate` lane

This creates a feedback loop: the development process teaches you how the platform should feel to its own users.

---

## 4. Workflow Examples

### 4.1 New Agent Implementation

```
1. Hermes: recall prior agent implementations, classify as COMPLICATED → plan lane
2. Claude Code: inspect BaseAgent, AGENTS.md, produce implementation plan + test plan
3. Handoff: planner-to-executor template filled with target files, acceptance criteria
4. Cursor: implement agent following BaseAgent contract, rules enforce guardrails
5. Copilot Pro: agent mode generates test scaffolding
6. Hermes: capture implementation pattern as reusable skill
```

### 4.2 Semantic Layer Extension

```
1. Gemini CLI: explore existing models, DATA_LAYER.md, TypeScript types → evidence summary
2. Claude Code: design new object type, migration, API surface
3. Handoff: planner-to-executor with Python model + TS type + migration + API endpoint
4. OpenCode + Go: parallel execution — Python model, TS type, SQL migration
5. Cursor: wire API endpoints, add tests
6. Hermes: governance brief for semantic layer change
```

### 4.3 Recurring Governance Review

```
1. OpenFang: weekly cron scans for drift between docs and implementation
2. Hermes: produces governance digest, surfaces assumption review queue
3. Claude Code: deep architecture review on flagged items
4. Cursor: fix identified issues
```

---

## 5. Anti-Patterns

| Anti-pattern | Why it fails | Fix |
|-------------|-------------|-----|
| Using all tools on the same step | Context fragmentation, conflicting edits | One lead tool per lane |
| Letting OpenCode become memory | OpenCode has no cross-session recall | Route through Hermes |
| Claude Code for plan AND implementation | Context window bloat, mixed concerns | Handoff packet between lanes |
| Automating unproven workflows in OpenFang | Automates mistakes at scale | Manual proof first, then automate |
| Skipping handoff packets | Next tool lacks constraints, produces drift | Always fill a template |

---

## 6. Configuration Files

Only files tracked in this repository are listed. Per-tool configuration for the IDE and CLI
agents above (Cursor rules, OpenCode agents, Hermes skills, OpenFang schedules) is personal and
kept out of version control; nothing here reads it, and a checkout without it is complete.

| File | Purpose |
|------|---------|
| `AGENTS.md` | Entry point for any coding agent: rules, flow, gate |
| `.gnomon/` | The agent harness surface: `config.toml` `[chain]`, `roles.toml`, `tools.toml`, `skills/`, `ci.sh` |
| `.github/copilot-instructions.md` | Pointer to `AGENTS.md` plus Copilot-specific essentials |
| `.github/ISSUE_TEMPLATE/*.yml` | Structured issues for Copilot Coding Agent |
| `templates/*.md` | Handoff packet templates |
| `prompts/*.yaml` | Prompt specifications for platform skills (not loaded at runtime; see `prompts/README.md`) |
