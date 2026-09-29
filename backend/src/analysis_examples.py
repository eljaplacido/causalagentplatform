"""
Example datasets for the analysis page, each simulated from a structural model whose
effect is set here -- so a first analysis can be checked against the truth.

Every example is deterministic (fixed seed), carries its own graph and question, and
states the true effect of the model it was drawn from. Two of them are there to show
what the platform does when the answer is not "an effect": a confounded comparison
where the naive difference is badly wrong, and a question the graph cannot answer
(a hidden common cause), where the correct output is a refusal.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

import numpy as np

Cell = float | int | str | bool | None


@dataclass(frozen=True)
class Example:
    id: str
    title: str
    summary: str
    graph: str
    treatment: str
    outcome: str
    design: Literal["observational", "randomized"]
    direction: Literal["increase", "decrease"]
    action: str
    minimum_worthwhile_effect: float
    #: The average effect of the model the rows were drawn from; None when the graph
    #: the example declares leaves it unanswerable (the point of that example).
    true_effect: float | None
    true_effect_note: str
    lesson: str
    columns: dict[str, list[Cell]] = field(repr=False)

    @property
    def n_rows(self) -> int:
        return len(next(iter(self.columns.values())))


def _sigmoid(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-x))


def _round(a: np.ndarray, digits: int = 3) -> list[Cell]:
    return [round(float(v), digits) for v in a]


def _email_campaign() -> Example:
    # Engaged, long-tenured customers were more likely to get the email *and* spend
    # more anyway: the raw difference is more than twice the effect (3.0).
    rng = np.random.default_rng(20260929)
    n = 2000
    engagement = rng.normal(0.0, 1.0, n)
    tenure = rng.uniform(0.0, 5.0, n)
    emailed = rng.binomial(1, _sigmoid(-0.3 + 0.9 * engagement + 0.2 * tenure))
    spend = 20.0 + 4.0 * engagement + 1.5 * tenure + 3.0 * emailed + rng.normal(0.0, 5.0, n)
    return Example(
        id="email-campaign",
        title="Did the email campaign raise spend?",
        summary="2,000 customers; the marketing team chose who got the email, favouring "
        "engaged, long-standing customers.",
        graph="engagement -> emailed; tenure -> emailed; engagement -> spend; "
        "tenure -> spend; emailed -> spend",
        treatment="emailed",
        outcome="spend",
        design="observational",
        direction="increase",
        action="send the campaign email to every customer",
        minimum_worthwhile_effect=1.0,
        true_effect=3.0,
        true_effect_note="spend rises by exactly 3.0 per emailed customer in the model the "
        "rows were drawn from",
        lesson="The raw difference between emailed and not-emailed customers is more than "
        "twice the true effect, inflated by who was chosen. Adjusting for engagement and "
        "tenure (the graph says why) recovers the effect.",
        columns={
            "engagement": _round(engagement),
            "tenure": _round(tenure),
            "emailed": [int(v) for v in emailed],
            "spend": _round(spend, 2),
        },
    )


def _checkout_ab() -> Example:
    rng = np.random.default_rng(20260930)
    n = 6000
    new_checkout = rng.binomial(1, 0.5, n)
    mobile = rng.binomial(1, 0.55, n)
    converted = rng.binomial(1, 0.10 + 0.03 * new_checkout + 0.04 * mobile)
    return Example(
        id="checkout-ab-test",
        title="A/B test: does the new checkout convert better?",
        summary="6,000 visitors randomly assigned to the old or the new checkout.",
        graph="new_checkout -> converted; mobile -> converted",
        treatment="new_checkout",
        outcome="converted",
        design="randomized",
        direction="increase",
        action="ship the new checkout to all visitors",
        minimum_worthwhile_effect=0.01,
        true_effect=0.03,
        true_effect_note="conversion probability rises by 3.0 percentage points in the model",
        lesson="Randomization identifies the effect with no adjustment; the analysis also "
        "checks that the split is the one the experiment intended (sample-ratio mismatch).",
        columns={
            "new_checkout": [int(v) for v in new_checkout],
            "mobile": [int(v) for v in mobile],
            "converted": [int(v) for v in converted],
        },
    )


def _discount_churn() -> Example:
    def logits(tickets: np.ndarray, premium: np.ndarray, discount: np.ndarray) -> np.ndarray:
        return -1.2 + 0.35 * tickets - 0.6 * premium - 0.7 * discount

    rng = np.random.default_rng(20261001)
    n = 3000
    tickets = rng.poisson(2.0, n)
    premium = rng.binomial(1, 0.3, n)
    discount = rng.binomial(1, _sigmoid(-1.0 + 0.45 * tickets - 0.5 * premium))
    churned = rng.binomial(1, _sigmoid(logits(tickets, premium, discount)))
    # The model's average effect on the churn probability, over a large independent
    # draw of the same population (the logistic model has no single coefficient for it).
    big = np.random.default_rng(1)
    t_big = big.poisson(2.0, 400_000)
    p_big = big.binomial(1, 0.3, 400_000)
    ate = float(
        np.mean(
            _sigmoid(logits(t_big, p_big, np.ones_like(t_big)))
            - _sigmoid(logits(t_big, p_big, np.zeros_like(t_big)))
        )
    )
    return Example(
        id="discount-churn",
        title="Does a retention discount reduce churn?",
        summary="3,000 customers; support offered the discount mostly to customers who had "
        "raised many tickets, the ones most likely to leave.",
        graph="support_tickets -> discount; premium -> discount; support_tickets -> churned; "
        "premium -> churned; discount -> churned",
        treatment="discount",
        outcome="churned",
        design="observational",
        direction="decrease",
        action="offer the retention discount to every customer",
        minimum_worthwhile_effect=0.03,
        true_effect=round(ate, 4),
        true_effect_note="the average change in churn probability in the model (computed "
        "over 400,000 draws of the same population)",
        lesson="Discounts went to the unhappy customers, so the raw comparison hides most of "
        "the effect: it understates the drop in churn by more than half. Adjusting for "
        "tickets and plan recovers it.",
        columns={
            "support_tickets": [int(v) for v in tickets],
            "premium": [int(v) for v in premium],
            "discount": [int(v) for v in discount],
            "churned": [int(v) for v in churned],
        },
    )


def _hidden_confounder() -> Example:
    rng = np.random.default_rng(20261002)
    n = 1500
    demand = rng.normal(0.0, 1.0, n)  # never recorded
    ad_spend = 10.0 + 3.0 * demand + rng.normal(0.0, 1.0, n)
    revenue = 100.0 + 2.0 * ad_spend + 15.0 * demand + rng.normal(0.0, 5.0, n)
    return Example(
        id="ad-spend-hidden-demand",
        title="Does ad spend drive revenue? (a question the data can't answer)",
        summary="1,500 weeks of ad spend and revenue. Seasonal demand raised both, and "
        "demand was never recorded.",
        graph="ad_spend -> revenue; ad_spend <-> revenue",
        treatment="ad_spend",
        outcome="revenue",
        design="observational",
        direction="increase",
        action="raise the ad budget",
        minimum_worthwhile_effect=0.5,
        true_effect=None,
        true_effect_note="the model's effect is 2.0 revenue per unit of spend, but with "
        "demand unrecorded no method can recover it from these columns",
        lesson="The graph declares an unmeasured common cause (<->). The correct output is a "
        "refusal that says what information would make the question answerable; a "
        "regression would report a confident, wrong number here.",
        columns={"ad_spend": _round(ad_spend, 2), "revenue": _round(revenue, 2)},
    )


_BUILDERS = (_email_campaign, _checkout_ab, _discount_churn, _hidden_confounder)
_CACHE: dict[str, Example] = {}


def examples() -> list[Example]:
    """Every example, built once per process."""
    if not _CACHE:
        for build in _BUILDERS:
            ex = build()
            _CACHE[ex.id] = ex
    return list(_CACHE.values())


def example(example_id: str) -> Example | None:
    examples()
    return _CACHE.get(example_id)
