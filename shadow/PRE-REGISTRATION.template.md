# Shadow round <N>: pre-registration (template)

> **Status: template.** Copy it to `shadow/rounds/<N>/PRE-REGISTRATION.md`, fill in every
> `<...>`, and commit it with the owner's approval **before the first study runs**. After that
> it changes only with a version bump and a changelog entry, as every bar in `benchmarks/`
> does. The bars below are proposals for the first round. They are deliberately conservative,
> and the owner sets the final ones.

## What this round measures

How the platform's verdicts on real decisions compare with what happened afterwards, and with
what people decided. No verdict in this round is acted on because of the platform.

## Studies

| id | proposed by | design (capability card) | expect | follow-up after |
|---|---|---|---|---|
| S-001 | <devbrain / agentcenter> | <observational, randomized, sequential, cluster_randomized, switchback, difference_in_differences> | <a verdict, or a named refusal> | <window> |

Planted cases, the ones the platform should refuse, are at least a quarter of the round. Each
names its refusal reason from the card's `refusals`.

## Rules fixed in advance

- **Verdicts are sealed** before any follow-up data exists. A study's `decision` block and
  `segment_by` are frozen at submission.
- **No emission.** Shadow decisions are never sent to the brain as
  `causalplatform.decision.recorded`.
- **Human decisions are recorded blind.** The people responsible decide without the
  platform's verdict in view, and their decision is written to `shadow.human_decision`.
- **Undecidable follow-ups** (the re-run refuses, for example because everyone was treated in
  the later window) are reported as undecidable, never dropped.
- **Nothing is deleted.** A failed round is committed with its results.

## Bars

```toml
[round]
version = 1
min_studies = 10
min_planted_share = 0.25

[bars]
# No confident wrong answer: an `act` or `act_with_approval` whose follow-up interval lies
# wholly on the harmful side of zero.
max_confident_wrong = 0
# Every planted case refused, with the expected reason.
min_planted_refused = 1.0
min_planted_reason_match = 0.75
# Disagreements with the human decision are reviewed, not scored as errors: each gets a
# written review in the round's directory.
disagreements_reviewed = 1.0
# When an agent drives the flow, every number in its prose passes causal_verify_narrative.
min_narratives_verified = 1.0

[reported_not_gated]
# Too few follow-ups in one round to gate them; reported with Clopper-Pearson intervals, as the
# calibration report does.
metrics = ["act_right_rate", "experiment_share", "refusal_share_by_reason", "agreement_with_human"]
```

## Changelog

- v1 (<date>): first round.
