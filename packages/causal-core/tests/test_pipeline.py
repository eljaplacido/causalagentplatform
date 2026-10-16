"""End-to-end behaviour of analyze(): results, refusals and failures."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
import pytest

import causal_core as cc
import causal_core.estimators as registry
from causal_core.contract import AbstentionReason, ResultStatus, Strategy
from causal_core.estimators import Fit, Prepared

from .conftest import CONFOUNDED, simulate

G = CONFOUNDED


def test_binary_backdoor_recovers_truth(confounded_binary: pd.DataFrame) -> None:
    out = cc.analyze(confounded_binary, G, treatment="X", outcome="Y")
    assert isinstance(out, cc.CausalResult), cc.report(out)
    assert out.status is ResultStatus.supported
    assert out.certificate.adjustment_set == ("W", "Z")
    assert out.primary.estimator == "aipw"
    assert out.primary.ci is not None and out.primary.ci.lo < 2.0 < out.primary.ci.hi
    assert {e.estimator for e in out.estimates} == {"aipw", "regression_adjustment", "ipw_hajek"}
    assert out.triangulation is not None and out.triangulation.agree
    assert out.sensitivity is not None and out.sensitivity.e_value and out.sensitivity.e_value > 1


def test_continuous_backdoor_uses_dml(confounded_continuous: pd.DataFrame) -> None:
    out = cc.analyze(confounded_continuous, G, treatment="X", outcome="Y")
    assert isinstance(out, cc.CausalResult)
    assert out.primary.estimator == "dml_plr"
    assert abs(out.primary.point - 2.0) < 4 * (out.primary.se or 1)


def test_treatment_levels_scale_continuous_effects(confounded_continuous: pd.DataFrame) -> None:
    out = cc.analyze(
        confounded_continuous, G, treatment="X", outcome="Y", treatment_levels=(0.0, 2.5)
    )
    assert isinstance(out, cc.CausalResult)
    assert abs(out.primary.point - 5.0) < 4 * (out.primary.se or 1)


def test_non_identified_refuses_with_what_would_help(confounded_binary: pd.DataFrame) -> None:
    out = cc.analyze(confounded_binary, "X -> Y; X <-> Y", treatment="X", outcome="Y")
    assert isinstance(out, cc.Abstention)
    assert out.reason is AbstentionReason.NON_IDENTIFIABLE
    assert out.certificate is not None and out.missing_information


def test_graph_nodes_missing_from_data_become_unmeasured(confounded_binary: pd.DataFrame) -> None:
    out = cc.analyze(confounded_binary.drop(columns=["Z"]), G, treatment="X", outcome="Y")
    assert isinstance(out, cc.Abstention)
    assert out.reason is AbstentionReason.NON_IDENTIFIABLE


def test_invalid_user_adjustment_set(confounded_binary: pd.DataFrame) -> None:
    data = confounded_binary.assign(
        M=confounded_binary["X"] + np.random.default_rng(1).normal(size=len(confounded_binary))
    )
    out = cc.analyze(data, G + "; X -> M -> Y", treatment="X", outcome="Y", adjust_for=("Z", "M"))
    assert isinstance(out, cc.Abstention)
    assert out.reason is AbstentionReason.INVALID_ADJUSTMENT_SET
    assert any("{W, Z}" in m or "{Z}" in m for m in out.missing_information)


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"rung": "counterfactual"}, AbstentionReason.RUNG_MISMATCH),
        ({"rung": "association"}, AbstentionReason.RUNG_MISMATCH),
        ({"expected_treated_share": 1.5}, AbstentionReason.INVALID_INPUT),
    ],
)
def test_query_level_refusals(
    confounded_binary: pd.DataFrame, kwargs: dict[str, object], reason: AbstentionReason
) -> None:
    out = cc.analyze(confounded_binary, G, treatment="X", outcome="Y", **kwargs)
    assert isinstance(out, cc.Abstention) and out.reason is reason


def test_bad_inputs_are_refusals_not_exceptions(confounded_binary: pd.DataFrame) -> None:
    cases = [
        cc.analyze(object(), G, treatment="X", outcome="Y"),
        cc.analyze(confounded_binary, "X -> -> Y", treatment="X", outcome="Y"),
        cc.analyze(confounded_binary, G, treatment="nope", outcome="Y"),
        cc.analyze(confounded_binary, "A -> B; B -> A", treatment="X", outcome="Y"),
    ]
    assert all(isinstance(o, cc.Abstention) for o in cases)
    assert all(o.reason is AbstentionReason.INVALID_INPUT for o in cases)


def test_missing_values_refused_unless_drop_declared(confounded_binary: pd.DataFrame) -> None:
    data = confounded_binary.copy()
    data.loc[:9, "Z"] = np.nan
    out = cc.analyze(data, G, treatment="X", outcome="Y")
    assert isinstance(out, cc.Abstention) and out.reason is AbstentionReason.DESIGN_CHECK_FAILED
    out = cc.analyze(data, G, treatment="X", outcome="Y", policy=cc.AnalysisPolicy(missing="drop"))
    assert isinstance(out, cc.CausalResult)
    assert out.diagnostics.n_dropped_missing == 10
    assert any("missing completely at random" in w for w in out.warnings)


def test_positivity_violation_is_refused() -> None:
    rng = np.random.default_rng(0)
    z = rng.normal(size=2000)
    x = (z > 0).astype(float)  # Z determines treatment
    y = x + z + rng.normal(size=2000)
    out = cc.analyze(
        pd.DataFrame({"Z": z, "X": x, "Y": y}), "Z -> X; Z -> Y; X -> Y", treatment="X", outcome="Y"
    )
    assert isinstance(out, cc.Abstention) and out.reason is AbstentionReason.POSITIVITY


def test_low_n_and_sample_ratio_mismatch() -> None:
    small = simulate("X -> Y", {("X", "Y"): 1.0}, n=20, binary=frozenset({"X"}))
    out = cc.analyze(small, "X -> Y", treatment="X", outcome="Y")
    assert isinstance(out, cc.Abstention) and out.reason is AbstentionReason.LOW_N
    data = simulate("X -> Y", {("X", "Y"): 1.0}, n=4000, binary=frozenset({"X"}))
    out = cc.analyze(
        data, "X -> Y", treatment="X", outcome="Y", design="randomized", expected_treated_share=0.3
    )
    assert isinstance(out, cc.Abstention) and out.reason is AbstentionReason.DESIGN_CHECK_FAILED
    assert "sample ratio" in out.detail


def test_weak_instrument_is_refused() -> None:
    rng = np.random.default_rng(0)
    n = 1000
    u, z = rng.normal(size=n), rng.normal(size=n)
    x = 0.02 * z + u + rng.normal(size=n)
    y = x + u + rng.normal(size=n)
    out = cc.analyze(
        pd.DataFrame({"Z": z, "X": x, "Y": y}), "Z -> X -> Y; X <-> Y", treatment="X", outcome="Y"
    )
    assert isinstance(out, cc.Abstention) and out.reason is AbstentionReason.WEAK_INSTRUMENT


def test_categorical_covariates_are_encoded() -> None:
    rng = np.random.default_rng(0)
    n = 3000
    region = rng.choice(["north", "south", "east"], size=n)
    shift = pd.Series(region).map({"north": 0.0, "south": 1.0, "east": -1.0}).to_numpy()
    x = (shift + rng.logistic(size=n) > 0).astype(float)
    y = 1.5 * x + 2 * shift + rng.normal(size=n)
    out = cc.analyze(
        pd.DataFrame({"R": region, "X": x, "Y": y}),
        "R -> X; R -> Y; X -> Y",
        treatment="X",
        outcome="Y",
    )
    assert isinstance(out, cc.CausalResult)
    assert abs(out.primary.point - 1.5) < 4 * (out.primary.se or 1)


def test_llm_edges_make_results_provisional_or_refused(confounded_binary: pd.DataFrame) -> None:
    graph = "Z -> X; Z -> Y; X -> Y; W -> Y [origin=llm_prior]"
    out = cc.analyze(confounded_binary, graph, treatment="X", outcome="Y")
    assert isinstance(out, cc.CausalResult) and out.status is ResultStatus.provisional
    out = cc.analyze(
        confounded_binary,
        graph,
        treatment="X",
        outcome="Y",
        policy=cc.AnalysisPolicy(require_approved_graph=True),
    )
    assert isinstance(out, cc.Abstention)
    assert out.reason is AbstentionReason.UNAPPROVED_ASSUMPTION


def test_negative_control_that_descends_from_treatment_is_refused(
    confounded_binary: pd.DataFrame,
) -> None:
    out = cc.analyze(
        confounded_binary.assign(N=confounded_binary["Y"]),
        G + "; X -> N",
        treatment="X",
        outcome="Y",
        negative_control_outcomes=("N",),
    )
    assert isinstance(out, cc.Abstention) and out.reason is AbstentionReason.INVALID_INPUT


def test_ci_width_policy(confounded_binary: pd.DataFrame) -> None:
    out = cc.analyze(
        confounded_binary,
        G,
        treatment="X",
        outcome="Y",
        policy=cc.AnalysisPolicy(max_ci_width=0.01),
    )
    assert isinstance(out, cc.Abstention) and out.reason is AbstentionReason.CI_TOO_WIDE


def test_general_id_without_estimator_is_a_typed_refusal() -> None:
    rng = np.random.default_rng(0)
    n = 500
    frame = pd.DataFrame({c: rng.normal(size=n) for c in ("W1", "W2", "X", "Y")})
    out = cc.analyze(frame, "W1 -> W2 -> X -> Y; W1 <-> X; W1 <-> Y", treatment="X", outcome="Y")
    assert isinstance(out, cc.Abstention) and out.reason is AbstentionReason.NO_ESTIMATOR
    assert out.certificate is not None and out.certificate.strategy is Strategy.id_general


@dataclass(frozen=True)
class _Broken:
    name: str = "broken"
    family: str = "test"
    strategies: frozenset[Strategy] = frozenset({Strategy.backdoor})
    treatment_kinds: frozenset[str] = frozenset({"binary"})
    priority: int = -1

    def fit(self, d: Prepared, policy: cc.AnalysisPolicy, rng: np.random.Generator) -> Fit:
        raise RuntimeError("estimator exploded")


def test_primary_estimator_crash_is_an_apparatus_failure(confounded_binary: pd.DataFrame) -> None:
    registry.register(_Broken())  # type: ignore[arg-type]
    try:
        out = cc.analyze(confounded_binary, G, treatment="X", outcome="Y")
    finally:
        registry.unregister("broken")
    assert isinstance(out, cc.ApparatusFailure)
    assert out.stage == "estimate" and "exploded" in out.detail


def test_registry_rejects_non_estimators() -> None:
    with pytest.raises(TypeError):
        registry.register(object())  # type: ignore[arg-type]


def test_table_inputs(confounded_binary: pd.DataFrame) -> None:
    pa = pytest.importorskip("pyarrow")
    as_dict = cc.analyze(confounded_binary.to_dict(orient="list"), G, treatment="X", outcome="Y")
    as_arrow = cc.analyze(pa.Table.from_pandas(confounded_binary), G, treatment="X", outcome="Y")
    assert isinstance(as_dict, cc.CausalResult) and isinstance(as_arrow, cc.CausalResult)
    assert as_dict.primary.point == pytest.approx(as_arrow.primary.point)


def test_randomized_experiment() -> None:
    data = simulate(
        "X -> Y; W -> Y", {("X", "Y"): 0.4, ("W", "Y"): 1.0}, n=4000, binary=frozenset({"X"})
    )
    out = cc.analyze(
        data,
        "X -> Y; W -> Y",
        treatment="X",
        outcome="Y",
        design="randomized",
        expected_treated_share=0.5,
    )
    assert isinstance(out, cc.CausalResult)
    assert out.certificate.strategy is Strategy.randomized
    assert out.primary.estimator == "difference_in_means"
    assert out.diagnostics.srm_p_value is not None and out.diagnostics.srm_p_value > 0.001


def test_polars_input(confounded_binary: pd.DataFrame) -> None:
    pl = pytest.importorskip("polars")
    out = cc.analyze(pl.from_pandas(confounded_binary), G, treatment="X", outcome="Y")
    ref = cc.analyze(confounded_binary, G, treatment="X", outcome="Y")
    assert isinstance(out, cc.CausalResult) and isinstance(ref, cc.CausalResult)
    assert out.primary.point == pytest.approx(ref.primary.point)


def test_att_differs_from_ate_under_heterogeneity() -> None:
    from causal_core.bench.t0 import _heterogeneous, _heterogeneous_att

    frame = _heterogeneous(4000, np.random.default_rng(3))
    g = "Z -> X; Z -> Y; X -> Y"
    ate = cc.analyze(frame, g, treatment="X", outcome="Y")
    att = cc.analyze(frame, g, treatment="X", outcome="Y", estimand="att")
    assert isinstance(ate, cc.CausalResult) and isinstance(att, cc.CausalResult)
    assert att.primary.estimand.startswith("ATT") and att.primary.estimator == "aipw"
    assert abs(ate.primary.point - 1.0) < 4 * (ate.primary.se or 1)
    assert abs(att.primary.point - _heterogeneous_att()) < 4 * (att.primary.se or 1)
    assert {e.estimator for e in att.estimates} == {"aipw", "regression_adjustment", "ipw_hajek"}


def test_att_needs_one_sided_overlap_only() -> None:
    """Many controls unlike any treated unit break ATE overlap but not ATT overlap."""
    rng = np.random.default_rng(0)
    n = 4000
    z = rng.normal(size=n)
    x = (3.0 * z - 4.0 + rng.logistic(size=n) > 0).astype(float)  # treated only at high z
    y = x + z + rng.normal(size=n)
    frame = pd.DataFrame({"Z": z, "X": x, "Y": y})
    g = "Z -> X; Z -> Y; X -> Y"
    ate = cc.analyze(frame, g, treatment="X", outcome="Y")
    att = cc.analyze(frame, g, treatment="X", outcome="Y", estimand="att")
    assert isinstance(ate, cc.Abstention) and ate.reason is AbstentionReason.POSITIVITY
    assert isinstance(att, cc.CausalResult), cc.report(att)


def test_att_with_continuous_treatment_is_refused(confounded_continuous: pd.DataFrame) -> None:
    out = cc.analyze(confounded_continuous, G, treatment="X", outcome="Y", estimand="att")
    assert isinstance(out, cc.Abstention) and out.reason is AbstentionReason.INVALID_INPUT


def test_att_with_iv_has_no_estimator() -> None:
    rng = np.random.default_rng(0)
    n = 2000
    u, z = rng.normal(size=n), rng.normal(size=n)
    x = (z + u + rng.logistic(size=n) > 0).astype(float)
    y = x + u + rng.normal(size=n)
    out = cc.analyze(
        pd.DataFrame({"Z": z, "X": x, "Y": y}),
        "Z -> X -> Y; X <-> Y",
        treatment="X",
        outcome="Y",
        estimand="att",
    )
    assert isinstance(out, cc.Abstention) and out.reason is AbstentionReason.NO_ESTIMATOR


def test_dependencies_among_adjusted_covariates_are_notes_not_failures() -> None:
    rng = np.random.default_rng(0)
    n = 3000
    a = rng.normal(size=n)
    b = 0.8 * a + rng.normal(size=n)  # the graph below wrongly says A and B are independent
    x = (a + b + rng.logistic(size=n) > 0).astype(float)
    y = x + a + b + rng.normal(size=n)
    frame = pd.DataFrame({"A": a, "B": b, "X": x, "Y": y})
    out = cc.analyze(frame, "A -> X; B -> X; A -> Y; B -> Y; X -> Y", treatment="X", outcome="Y")
    assert isinstance(out, cc.CausalResult)
    assert out.status is ResultStatus.supported
    assert any("omits dependencies among adjustment covariates" in w for w in out.warnings)


def test_low_effective_sample_is_warned() -> None:
    """LaLonde-like: many controls unlike the treated, and only a few comparable ones."""
    rng = np.random.default_rng(1)
    z = np.concatenate([rng.normal(2, 0.5, 200), rng.normal(-2, 1, 2000), rng.normal(2, 0.5, 25)])
    x = np.concatenate([np.ones(200), np.zeros(2025)])
    y = x + z + rng.normal(size=z.size)
    out = cc.analyze(
        pd.DataFrame({"Z": z, "X": x, "Y": y}),
        "Z -> X; Z -> Y; X -> Y",
        treatment="X",
        outcome="Y",
        estimand="att",
    )
    assert isinstance(out, cc.CausalResult), cc.report(out)
    assert out.diagnostics.propensity is not None
    assert out.diagnostics.propensity.ess_control < 50
    assert any("effective sample size" in w for w in out.warnings)


def test_sparse_binary_covariates_do_not_blow_up_aipw() -> None:
    """Regression test for the IHDP finding: a binary covariate that is constant inside the
    treated arm made cross-fitted OLS near-singular and AIPW return ~1e16."""
    rng = np.random.default_rng(0)
    n = 800
    z = rng.normal(size=n)
    rare = (rng.random(n) < 0.03).astype(float)  # 3% prevalence
    x = (0.5 * z - 2.0 * rare + rng.logistic(size=n) > 0).astype(float)
    x[rare == 1] = 0.0  # never treated: constant (0) within the treated arm
    y = 2.0 * x + z + 3.0 * rare + rng.normal(size=n)
    frame = pd.DataFrame({"Z": z, "R": rare, "X": x, "Y": y})
    out = cc.analyze(
        frame, "Z -> X; R -> X; Z -> Y; R -> Y; X -> Y", treatment="X", outcome="Y", estimand="att"
    )
    assert isinstance(out, cc.CausalResult), cc.report(out)
    for e in out.estimates:
        assert abs(e.point - 2.0) < 1.0 and (e.se or 0) < 1.0, e


def test_absurd_standard_error_is_an_apparatus_failure(confounded_binary: pd.DataFrame) -> None:
    @dataclass(frozen=True)
    class _Unstable:
        name: str = "unstable"
        family: str = "test"
        strategies: frozenset[Strategy] = frozenset({Strategy.backdoor})
        treatment_kinds: frozenset[str] = frozenset({"binary"})
        priority: int = -1

        def fit(self, d: Prepared, policy: cc.AnalysisPolicy, rng: np.random.Generator) -> Fit:
            from causal_core.contract import EstimateRecord

            rec = EstimateRecord(
                estimator=self.name,
                family=self.family,
                estimand="x",
                point=-2e16,
                se=2e16,
                ci=None,
                n_used=d.n,
            )
            return Fit(record=rec)

    registry.register(_Unstable())  # type: ignore[arg-type]
    try:
        out = cc.analyze(confounded_binary, G, treatment="X", outcome="Y")
    finally:
        registry.unregister("unstable")
    assert isinstance(out, cc.ApparatusFailure)
    assert out.error_type == "NumericalInstabilityError" and out.stage == "estimate"


def test_the_certificate_names_the_unreviewed_edges_a_language_model_proposed(
    confounded_binary: pd.DataFrame,
) -> None:
    """Only proposed llm_prior edges: not a person's unreviewed edge, not a reviewed LLM one."""
    graph = (
        "Z -> X; Z -> Y; X -> Y; W -> Y [origin=llm_prior]; V -> Y [status=proposed]; "
        "U -> Y [origin=llm_prior, status=approved]"
    )
    out = cc.analyze(confounded_binary, graph, treatment="X", outcome="Y")
    assert isinstance(out, cc.CausalResult) and out.status is ResultStatus.provisional
    assert set(out.certificate.unreviewed_edges) == {"W -> Y", "V -> Y"}
    assert out.certificate.unreviewed_llm_edges == ("W -> Y",)
