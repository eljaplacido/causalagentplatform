# Changelog -- causal-mcp

Earlier version numbers were development milestones inside the repository and were never
published.

## 0.5.0 -- first PyPI release (unreleased)

- An MCP server (Python SDK v2), stdio by default and stateless HTTP with the `[http]` extra.
- Four tools always: `causal_check_graph`, `causal_identify`, `causal_analyze`,
  `causal_verify_narrative`. Two more with the `[decision]` extra: `causal_decide` and
  `causal_impact`.
- Every tool is read-only and idempotent and returns typed structured output; the full claim
  contract is served as the `causal://schemas/*` resources.
- The tool listing inlines the outcome contract once (in `causal_analyze`); `causal_decide`'s outcome is
  listed as the contract's envelope naming `causal://schemas/outcome`, so `tools/list` stays
  well under the stdio client's 64 KiB line limit (septa-55).
