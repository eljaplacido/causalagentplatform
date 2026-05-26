# Structured Issue for Copilot Coding Agent

> Use this template when creating a GitHub issue intended for the Copilot Coding Agent
> to convert into a pull request. Keep scope tight and criteria verifiable.

## Title

<!-- Short, action-oriented: "Add X to Y" or "Fix Z in W" -->

## Type

- [ ] Implementation (new code)
- [ ] Test addition
- [ ] Bug fix
- [ ] Refactor
- [ ] Documentation

## Description

<!-- 2-3 sentences: what needs to happen and why -->

## Target Files

<!-- Be explicit — helps the coding agent scope its work -->

- [ ] `path/to/file1.py`
- [ ] `path/to/file2.ts`

## Acceptance Criteria

<!-- Numbered, independently verifiable -->

1.
2.
3.

## Test Expectations

<!-- What tests should pass after this change? -->

- [ ] Existing tests still pass: `cd backend && python -m pytest tests/ -q`
- [ ] New test added for: <!-- specific behavior -->
- [ ] Type check passes: `cd frontend && npm run typecheck`

## Platform Constraints

<!-- Which rules apply? Reference .cursor/rules/ if helpful -->

- [ ] EpistemicState contract preserved
- [ ] No hardcoded model names
- [ ] Guardian write paths enforced (if applicable)

## Labels

<!-- Apply these labels on the GitHub issue -->

`lane/implement`, `copilot-ready`
