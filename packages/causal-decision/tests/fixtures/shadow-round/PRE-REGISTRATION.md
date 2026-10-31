# Shadow round "fixture": pre-registration (test fixture, simulated data)

> **A test fixture, not a round.** `packages/causal-decision/tests/test_shadow_round.py` copies
> this directory, writes each study's data from a structural model whose effect it sets, and
> holds the scorecard to that known truth. Nothing here is about a real decision. It follows
> `shadow/PRE-REGISTRATION.template.md`; only `min_studies` is smaller, so a five-study fixture
> can pass.

## Studies

| id | proposed by | design | expect | follow-up after | truth set by the test |
|---|---|---|---|---|---|
| S-001 | devbrain | randomized | a verdict | 30d | +1.0 before; +1.0 (holds) or -1.0 (reversed) after |
| S-002 | agentcenter | observational | a verdict | 30d | +1.0 before; everyone treated after (undecidable) |
| S-003 | devbrain | observational | NON_IDENTIFIABLE | 30d | a confounder `u` with no data column |
| S-004 | agentcenter | randomized | DESIGN_CHECK_FAILED | 30d | every unit treated |
| S-005 | devbrain | randomized | a verdict | 30d | -1.0 before and after (harmful) |

## Bars

```toml
[round]
version = 1
min_studies = 5
min_planted_share = 0.25

[bars]
max_confident_wrong = 0
min_planted_refused = 1.0
min_planted_reason_match = 0.75
disagreements_reviewed = 1.0
min_narratives_verified = 1.0

[reported_not_gated]
metrics = ["act_right_rate", "experiment_share", "refusal_share_by_reason", "agreement_with_human"]
```

## Changelog

- v1 (2026-10-31): the fixture for septa-96.
