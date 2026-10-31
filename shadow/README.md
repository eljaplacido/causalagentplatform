# Shadow runs: the context pack

The pre-registered gates in [`benchmarks/`](../benchmarks) prove the kernel's numbers on data
simulated from models whose effects are known. They cannot say how the platform does on the
decisions it is meant for. A **shadow run** measures that, without risk:

- the platform analyses a real question and records its verdict;
- nobody acts on that verdict;
- after a fixed window the same analysis is re-run on the later data, and the sealed prediction
  is scored against what happened.

This directory is what the systems that propose shadow studies need in order to propose good
ones. It is written for **devbrain** (its own project on the RTX 5090 hub) and **agentcenter**
with its Oracle (on gx10, next to the platform). Both know which decisions are being made and
which data exists, and the platform does not.

| File | What it is |
|---|---|
| [`capability-card.json`](capability-card.json) | Each design the kernel answers, in machine-readable form: when it fits, the query fields it needs, the data shape, the refusals to expect, and the gate that proves it. It also holds the refusal reasons and the four verdicts in plain words, and what the platform cannot do yet. `packages/causal-decision/tests/test_shadow_pack.py` fails when the card disagrees with the kernel's contract. |
| [`study-template.json`](study-template.json) | One shadow study, written as an Oracle hypothesis spec (`arms`, `graph`, `design`, `estimand`, `decision`) with a `shadow` block for the platform. Its query validates as a `CausalQuery`, and its `decision` validates as a `DecisionRequest`. |
| [`PRE-REGISTRATION.template.md`](PRE-REGISTRATION.template.md) | The draft pass bars for a round of shadow studies. A round is judged against bars committed before its first run, as every gate here is. |

## Roles

| Who | Where | Does |
|---|---|---|
| devbrain | RTX 5090 hub | Proposes studies from the decisions and data it sees across projects. It reads this pack over Tailscale, and its proposals come back the same way. |
| agentcenter and its Oracle | gx10 | Proposes studies from its hypotheses (`/api/hypotheses`). It already sends them to the platform as prefilled analyses (`GET /api/v1/brain/suggestions`) and receives decisions back. |
| The owner | | Approves each round's pre-registration. The Oracle's pre-registrations and verdicts stay human-gated (its G2 and G6). |
| The platform | gx10 | Runs each study, seals its verdict, takes the follow-up after the window, and reports. |

## A round, in order

1. **Propose 10–20 studies**, each filled in from `study-template.json`. Spread them across the
   designs in the card, and plant some questions the platform should refuse: a confounder with
   no data column, a segment where every unit was treated, a rollout with no comparison group.
   Each planted case names its expected refusal in `shadow.expect`.
2. **Pre-register.** Copy `PRE-REGISTRATION.template.md` to the round's directory, fill in the
   study list and the bars, and have the owner approve it. Commit it before the first run.
3. **Run in shadow.** Each study goes through the normal flow: check the graph, then identify,
   then decide. The verdict is recorded with `shadow.tag`. A shadow verdict is never emitted to
   the brain as a decision (`causalplatform.decision.recorded`), and nobody acts on it because
   of the platform.
4. **Follow up** when `shadow.follow_up_after` has passed. The follow-up is the same analysis
   re-run on the later window, so a follow-up the data cannot settle is recorded as
   undecidable rather than guessed.
5. **Score** against the bars. A failed round is kept, never deleted, as every benchmark run is.

## Running a round

A round lives in `shadow/rounds/<N>/`: its `PRE-REGISTRATION.md`, one file per study under
`studies/`, each study's data at the paths its `shadow.data` names (relative to the round
directory; CSV, Parquet, JSON or JSONL), and a `reviews/<id>.md` for each disagreement with the
human decision. Then, in two steps, because verdicts are sealed before any follow-up data exists:

```bash
python -m causal_decision.shadow_round seal  shadow/rounds/<N>   # step 3: check, identify, decide, seal
python -m causal_decision.shadow_round score shadow/rounds/<N>   # steps 4-5: due follow-ups, the bars
```

`seal` refuses a round whose follow-up data already exists, and seals a round once per
pre-registration version, into `results/`. `score` refuses a seal whose bars or studies changed
afterwards (only `shadow.human_decision` and `shadow.human_acted` may be filled in later), takes a
follow-up only once it is due, never re-measures one, and writes a new file under `scorecards/`
every time, failed or not. It exits 0 when the round passed, 1 when it failed and 2 while bars are
pending. `packages/causal-decision/README.md` ("Trying it in shadow") has the details.

A study can also be decided through the API: `POST /api/v1/decisions` with `"shadow": "<tag>"`
seals it into the platform's ledger as a shadow decision. Either way, a shadow decision is never
emitted to agentcenter as `causalplatform.decision.recorded` -- the connector refuses the sealed
record whatever `AGENTCENTER_EMIT` says (`causal_decision.shadow.emittable`) -- and it is listed
apart from real decisions: `GET /api/v1/decisions?scope=shadow` (or `tag=<tag>`), the Shadow
switch on the `/decisions` page, and a calibration report of its own (`scope=shadow`).

No round on real decisions has run yet.
