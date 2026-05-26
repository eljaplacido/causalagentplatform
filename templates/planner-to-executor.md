# Handoff: Plan → Implement

> Fill this template when transitioning from the `plan` lane to the `implement` lane.
> The executor (Cursor / OpenCode / Copilot) should be able to work from this alone.

## Mission

<!-- One sentence: what is being built and why -->

## Target Files

<!-- List every file to create or modify. Be specific. -->

| File | Action | Description |
|------|--------|-------------|
| | create / modify | |

## Acceptance Criteria

<!-- Numbered list. Each criterion must be independently verifiable. -->

1.
2.
3.

## EpistemicState Constraints

<!-- Which platform contracts apply to this change? -->

- [ ] Preserves EpistemicState in/out contract
- [ ] Guardian-gated write paths included
- [ ] Cynefin routing not bypassed
- [ ] Audit trail write verified
- [ ] Provider-agnostic (no hardcoded model names)

## Scope Boundaries

<!-- What is explicitly OUT of scope for this packet? -->

-

## Verification Commands

<!-- Commands the executor should run to verify the change works -->

```bash
# Example:
# cd backend && python -m pytest tests/ -q
# cd frontend && npm run typecheck
```

## Dependencies

<!-- What must exist before this work can start? -->

-

## Risk Notes

<!-- Anything the executor should be cautious about -->

-

## Source

- **Planner tool:** <!-- Claude Code / Antigravity / Gemini CLI -->
- **Lane:** plan → implement
- **Date:**
- **Session ref:** <!-- Hermes session ID if available -->
