"""A shadow round, sealed and scored against bars it pre-registered (septa-96).

The fixture round (tests/fixtures/shadow-round) is five studies whose data this file
simulates from structural models with the effect set by construction, so every
judgement the scorecard makes can be checked against the truth:

    S-001  randomized,     y = 1 + 1.0 t + e         -> act; after: +1.0 (holds) or -1.0 (reversed)
    S-002  observational,  y = z + 1.0 t + e, z -> t -> act; after: everyone treated (undecidable)
    S-003  planted: a confounder u with no data column -> NON_IDENTIFIABLE
    S-004  planted: every unit treated                 -> NO_VARIATION (an experiment)
    S-005  randomized,     y = 1 - 1.0 t + e         -> refuse; after: -1.0 again

Preconditions: a copy of the fixture directory with its decision-window data written;
`now` carries a UTC offset.

Postconditions (shadow/README.md, shadow/PRE-REGISTRATION.template.md, the module's
docstring):
  - `seal` analyses and decides every study through the kernel and seals each verdict,
    tagged with the round's shadow tag, into a chain that verifies; no sealed record
    can be emitted; a result's follow-up is planned with its due date;
  - verdicts are sealed before follow-up data exists, once per pre-registration version;
  - `score` takes a follow-up only once it is due and its data exists, and an earlier
    measurement stands; the pre-registration and the studies' frozen fields cannot
    change after the seal, and a tampered seal is refused;
  - an acted verdict whose follow-up interval lies wholly below zero is confidently
    wrong, and fails a round whose bar is 0; a planted study counts as refused only
    with a kernel refusal, and as matched only with the expected reason;
  - a disagreement with the human decision leaves the round pending until reviewed;
  - every scorecard, failed or not, is written to a new file, and none is overwritten.

Undefined here: the exact estimates (asserted only through intervals and the set
truth), file timestamps, the order of keys in the JSON.
"""

from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from causal_decision import Verdict
from causal_decision.ledger import verify_chain
from causal_decision.shadow import ShadowEmissionError, emittable
from causal_decision.shadow_round import (
    RoundError,
    Scorecard,
    load_prereg,
    score,
    seal,
)

FIXTURE = Path(__file__).parent / "fixtures" / "shadow-round"
T0 = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)
DUE = T0 + timedelta(days=30)
N = 400
TRUTH_BEFORE = {"S-001": 1.0, "S-002": 1.0, "S-005": -1.0}


def _rct(effect: float, seed: int, share: float = 0.5) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    t = (rng.random(N) < share).astype(int)
    return pd.DataFrame({"t": t, "y": 1.0 + effect * t + rng.normal(0, 1, N)})


def _confounded(effect: float, seed: int, everyone: bool = False) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    z = rng.normal(size=N)
    p = np.ones(N) if everyone else 1 / (1 + np.exp(-z))
    t = (rng.random(N) < p).astype(int)
    return pd.DataFrame({"z": z, "t": t, "y": z + effect * t + rng.normal(0, 1, N)})


def _write(round_dir: Path, name: str, frame: pd.DataFrame) -> None:
    (round_dir / "data").mkdir(exist_ok=True)
    frame.to_csv(round_dir / "data" / name, index=False)


@pytest.fixture
def round_dir(tmp_path: Path) -> Path:
    """The fixture round with its decision-window data, and no follow-up data yet."""
    d = tmp_path / "fixture"
    shutil.copytree(FIXTURE, d)
    _write(d, "S-001-before.csv", _rct(1.0, seed=1))
    _write(d, "S-002-before.csv", _confounded(1.0, seed=2))
    _write(d, "S-003-before.csv", _confounded(1.0, seed=3)[["t", "y"]])  # u is never measured
    _write(d, "S-004-before.csv", _rct(1.0, seed=4, share=1.0))
    _write(d, "S-005-before.csv", _rct(-1.0, seed=5))
    return d


def _follow_ups(d: Path, s001_effect: float) -> None:
    _write(d, "S-001-after.csv", _rct(s001_effect, seed=11))
    _write(d, "S-002-after.csv", _confounded(1.0, seed=12, everyone=True))
    _write(d, "S-005-after.csv", _rct(-1.0, seed=15))


def _review(d: Path, study: str) -> None:
    (d / "reviews").mkdir(exist_ok=True)
    (d / "reviews" / f"{study}.md").write_text(
        "The owner did not offer the discount because of budget, not evidence.\n"
    )


def _near(ci: tuple[float, float] | None, truth: float) -> bool:
    """The set effect lies within twice the 95% half-width (about 3.9 SE) of the estimate.

    Not "inside the 95% interval": one fixed-seed draw misses that one time in twenty
    by design (S-002 at seed 2 does, by 0.0006). Coverage is what the T0 and C0 gates
    measure over hundreds of draws; here the truth only has to be unmistakably close.
    """
    assert ci is not None
    mid, half = (ci[0] + ci[1]) / 2, (ci[1] - ci[0]) / 2
    return abs(mid - truth) < 2 * half


def _bars(card: Scorecard) -> dict[str, str]:
    return {b.name: b.status for b in card.bars}


def test_seal_decides_every_study_and_seals_it_as_shadow(round_dir: Path) -> None:
    path, sealed = seal(round_dir, now=T0, git="test")
    assert path.name == "2026-10-01-v2-run1.json"
    assert path.parent == round_dir / "results"

    by_id = {s.id: s for s in sealed.studies}
    # The verdicts the set effects call for (minimum worthwhile effect 0.3, risk low).
    assert by_id["S-001"].verdict is Verdict.act
    assert by_id["S-002"].verdict is Verdict.act
    assert by_id["S-005"].verdict is Verdict.refuse
    assert by_id["S-003"].refusal_reason == "NON_IDENTIFIABLE"
    # A treatment that never varied is NO_VARIATION since contract 0.15.0 (#107), and its
    # verdict is an experiment, not a refusal: the kernel still refused to estimate.
    assert by_id["S-004"].refusal_reason == "NO_VARIATION"
    assert by_id["S-004"].verdict is Verdict.experiment
    assert by_id["S-004"].outcome_kind == "refusal"
    # The graph alone identifies S-003 (u is a node); the data, which lacks u, does not.
    assert by_id["S-003"].identification.identified
    assert by_id["S-003"].outcome_kind == "refusal"

    # Every sealed estimate is the effect set in the simulation (benefit = effect, since
    # every direction is increase).
    for sid, truth in TRUTH_BEFORE.items():
        planned = by_id[sid].followup
        assert planned is not None and _near(planned.predicted_ci, truth), sid

    # Refusals predicted nothing, so nothing is planned for them.
    assert by_id["S-003"].followup is None and by_id["S-004"].followup is None
    assert {s.follow_up_due for s in sealed.studies} == {DUE.isoformat()}

    chain = verify_chain(list(sealed.records))
    assert chain.ok and chain.length == 5 and chain.head_digest == sealed.chain_head
    for record in sealed.records:
        assert record.shadow == "shadow-fixture"
        assert record.review_by == DUE.isoformat()
        with pytest.raises(ShadowEmissionError):
            emittable(record)
    assert json.loads(path.read_text())["tag"] == "shadow-fixture"


def test_a_round_that_holds_passes_once_its_follow_ups_and_reviews_are_in(
    round_dir: Path,
) -> None:
    seal(round_dir, now=T0, git="test")

    # Before the window has passed nothing is measured, whatever data exists.
    _follow_ups(round_dir, s001_effect=1.0)
    _, early = score(round_dir, now=DUE - timedelta(days=1), git="test")
    assert early.status == "pending"
    assert {s.followup for s in early.studies if not s.planted} == {"not_due"}
    assert _bars(early)["max_confident_wrong"] == "pending"

    _, card = score(round_dir, now=DUE, git="test")
    by_id = {s.id: s for s in card.studies}
    # S-001: the effect held (+1.0); the measured interval covers it and clears the bar.
    r1 = by_id["S-001"].realization
    assert r1 is not None and r1.decidable and r1.realized_ci is not None
    assert _near(r1.realized_ci, 1.0)
    assert r1.cleared_worthwhile is True and by_id["S-001"].confident_wrong is False
    # S-002: rolled out to everyone afterwards, so the later data cannot say.
    r2 = by_id["S-002"].realization
    assert r2 is not None and not r2.decidable
    assert r2.undecidable_reason == "NO_VARIATION"
    # S-005 was refused, so it cannot be confidently wrong; its effect is still harmful.
    r5 = by_id["S-005"].realization
    assert r5 is not None and r5.realized_ci is not None
    assert _near(r5.realized_ci, -1.0)
    assert by_id["S-005"].confident_wrong is None

    bars = _bars(card)
    assert bars["max_confident_wrong"] == "pass"
    assert bars["min_planted_refused"] == "pass"
    assert bars["min_planted_reason_match"] == "pass"
    assert bars["min_narratives_verified"] == "not_applicable"
    # S-002: the platform said act, the people responsible did not -> a review is owed.
    assert by_id["S-002"].agrees_with_human is False and by_id["S-002"].reviewed is False
    assert by_id["S-005"].agrees_with_human is True
    assert bars["disagreements_reviewed"] == "pending"
    assert card.status == "pending"

    _review(round_dir, "S-002")
    path, final = score(round_dir, now=DUE + timedelta(days=1), git="test")
    assert final.status == "passed", [b for b in final.bars if b.status != "pass"]
    # The follow-ups taken by the earlier scoring stand; they are not re-measured.
    assert {s.measured_in for s in final.studies if s.realization} == {"2026-10-31-v2-run1.json"}
    assert path.name == "2026-11-01-v2-run1.json"
    assert final.calibration.n_decisions == 3 and final.calibration.n_undecidable == 1
    # Five studies: every rate is refused below ten, with its counts kept.
    assert final.reported["act_right_rate"]["rate"] is None
    assert final.reported["act_right_rate"]["successes"] == 1
    assert final.reported["refusal_share_by_reason"]["counts"] == {
        "NON_IDENTIFIABLE": 1, "NO_VARIATION": 1,
    }
    assert final.reported["experiment_share"]["successes"] == 2  # S-003 and S-004


def test_a_reversed_effect_fails_the_round_and_the_failed_scorecard_is_kept(
    round_dir: Path,
) -> None:
    seal(round_dir, now=T0, git="test")
    _follow_ups(round_dir, s001_effect=-1.0)  # the action now harms: set by construction
    _review(round_dir, "S-002")

    first, card = score(round_dir, now=DUE, git="test")
    s001 = next(s for s in card.studies if s.id == "S-001")
    assert s001.verdict is Verdict.act
    assert s001.realization is not None and s001.realization.realized_ci is not None
    assert s001.realization.realized_ci[1] < 0  # wholly harmful
    assert _near(s001.realization.realized_ci, -1.0)
    assert s001.confident_wrong is True
    wrong = next(b for b in card.bars if b.name == "max_confident_wrong")
    assert wrong.status == "fail" and wrong.value == 1
    assert card.status == "failed"
    kept = first.read_text()

    # Scoring again writes a new file and leaves the failed one as it was.
    second, again = score(round_dir, now=DUE, git="test")
    assert second != first and second.name == "2026-10-31-v2-run2.json"
    assert first.read_text() == kept
    assert again.status == "failed"
    # A friendlier window cannot replace the first measurement.
    _follow_ups(round_dir, s001_effect=1.0)
    _, third = score(round_dir, now=DUE + timedelta(days=2), git="test")
    assert next(s for s in third.studies if s.id == "S-001").confident_wrong is True
    assert third.status == "failed"


def test_follow_up_data_present_at_seal_is_refused(round_dir: Path) -> None:
    _follow_ups(round_dir, s001_effect=1.0)
    with pytest.raises(RoundError, match="sealed before any follow-up data exists"):
        seal(round_dir, now=T0, git="test")
    assert not (round_dir / "results").exists()


def test_a_round_is_sealed_once_per_version(round_dir: Path) -> None:
    seal(round_dir, now=T0, git="test")
    with pytest.raises(RoundError, match="already sealed"):
        seal(round_dir, now=T0 + timedelta(days=1), git="test")


def test_bars_cannot_move_after_the_seal(round_dir: Path) -> None:
    seal(round_dir, now=T0, git="test")
    prereg = round_dir / "PRE-REGISTRATION.md"
    prereg.write_text(prereg.read_text().replace("max_confident_wrong = 0",
                                                 "max_confident_wrong = 1"))
    with pytest.raises(RoundError, match="changed since the round was sealed"):
        score(round_dir, now=DUE, git="test")


def test_a_study_is_frozen_except_for_the_human_decision(round_dir: Path) -> None:
    seal(round_dir, now=T0, git="test")
    path = round_dir / "studies" / "S-003.json"
    study = json.loads(path.read_text())
    study["shadow"]["human_acted"] = True  # filled in after the seal: allowed
    path.write_text(json.dumps(study))
    score(round_dir, now=DUE, git="test")

    study["decision"]["minimum_worthwhile_effect"] = 5.0  # re-aimed after the seal: refused
    path.write_text(json.dumps(study))
    with pytest.raises(RoundError, match="S-003 has changed since it was sealed"):
        score(round_dir, now=DUE, git="test")


def test_a_tampered_seal_is_refused(round_dir: Path) -> None:
    path, _ = seal(round_dir, now=T0, git="test")
    doc = json.loads(path.read_text())
    doc["records"][4]["decision"]["verdict"] = "act"  # S-005 was refused
    path.write_text(json.dumps(doc))
    with pytest.raises(RoundError, match="does not verify"):
        score(round_dir, now=DUE, git="test")


def test_an_unverified_agent_narrative_fails_its_bar(round_dir: Path) -> None:
    for sid, text in (("S-001", "The analysis found an effect."),
                      ("S-005", "The fee lowers conversion by 37.5 points.")):  # an invented number
        path = round_dir / "studies" / f"{sid}.json"
        study = json.loads(path.read_text())
        study["shadow"]["narrative"] = text
        path.write_text(json.dumps(study))
    _, sealed = seal(round_dir, now=T0, git="test")
    told = {s.id: s.narrative_verified for s in sealed.studies}
    assert told["S-001"] is True and told["S-005"] is False
    _, card = score(round_dir, now=DUE, git="test")
    bar = next(b for b in card.bars if b.name == "min_narratives_verified")
    assert (bar.status, bar.numerator, bar.denominator) == ("fail", 1, 2)
    assert card.status == "failed"


def test_too_small_a_round_fails_its_design_bars(round_dir: Path) -> None:
    for sid in ("S-003", "S-004"):  # no planted case left
        (round_dir / "studies" / f"{sid}.json").unlink()
    seal(round_dir, now=T0, git="test")
    _, card = score(round_dir, now=DUE, git="test")
    bars = {b.name: b for b in card.bars}
    assert bars["min_studies"].status == "fail"
    assert bars["min_planted_share"].status == "fail"
    # No planted study: the refusal bars inspected nothing, which is not a pass.
    assert bars["min_planted_refused"].status == "fail"
    assert bars["min_planted_refused"].denominator == 0
    assert card.status == "failed"


def test_a_preregistration_must_set_every_bar(tmp_path: Path) -> None:
    d = tmp_path / "r"
    d.mkdir()
    text = (FIXTURE / "PRE-REGISTRATION.md").read_text()
    (d / "PRE-REGISTRATION.md").write_text(text.replace("disagreements_reviewed = 1.0\n", ""))
    with pytest.raises(RoundError, match="missing \\['disagreements_reviewed'\\]"):
        load_prereg(d)
    (d / "PRE-REGISTRATION.md").write_text(text.replace("max_confident_wrong = 0",
                                                        "max_confident_wrong = 0\nmax_vibes = 1"))
    with pytest.raises(RoundError, match="unknown \\['max_vibes'\\]"):
        load_prereg(d)
    bars, sha = load_prereg(FIXTURE)
    assert bars.min_studies == 5 and bars.bars["max_confident_wrong"] == 0
    assert sha.startswith("sha256:")


def test_the_command_line_exit_codes(round_dir: Path) -> None:
    from causal_decision.shadow_round import main

    assert main(["seal", str(round_dir)]) == 0
    assert main(["seal", str(round_dir)]) == 3  # already sealed: refused, nothing written
    assert main(["score", str(round_dir)]) == 2  # nothing is due yet: pending
    assert len(list((round_dir / "scorecards").glob("*.json"))) == 1


def test_a_study_from_another_population_is_sealed_as_an_experiment_there(
    round_dir: Path,
) -> None:
    """Evidence from one population is not a verdict about another (#107, in a shadow round).

    S-001 is re-declared before the seal: its data come from the local lane and the action
    targets the hosted lane. The set effect (+1.0) would call for act; with no transport
    analysis the verdict is capped at an experiment run in the target, the sealed text says
    "not transported", and the study is not scored as acted -- so a reversed effect afterwards
    cannot make it confidently wrong, and its disagreement with the people who acted is owed
    a review.
    """
    path = round_dir / "studies" / "S-001.json"
    study = json.loads(path.read_text())
    study["query"]["population"] = "local lane"
    study["query"]["target_population"] = "hosted lane"
    path.write_text(json.dumps(study, indent=2))

    _, sealed = seal(round_dir, now=T0, git="test")
    s001 = next(s for s in sealed.studies if s.id == "S-001")
    assert s001.verdict is Verdict.experiment
    assert "not transported" in s001.explanation
    record = sealed.records[0]
    assert record.decision.evidence.population == "local lane"
    assert record.decision.evidence.target_population == "hosted lane"
    assert record.decision.experiment is not None
    assert record.decision.experiment.population == "hosted lane"
    assert verify_chain(list(sealed.records)).ok

    _follow_ups(round_dir, s001_effect=-1.0)  # it would have harmed: set by construction
    _, card = score(round_dir, now=DUE, git="test")
    scored = next(s for s in card.studies if s.id == "S-001")
    assert scored.confident_wrong is None
    assert scored.agrees_with_human is False and scored.reviewed is False
    assert next(b for b in card.bars if b.name == "max_confident_wrong").status == "pass"
