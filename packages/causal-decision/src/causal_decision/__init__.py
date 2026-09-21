"""
causal-decision: is an agent's proposed action causally justified enough to act on?

    >>> from causal_decision import decide, DecisionRequest
    >>> out = causal_core.analyze(df, graph, treatment="playbook", outcome="minutes_to_resolve")
    >>> d = decide(out, DecisionRequest(action="apply playbook X", outcome="minutes_to_resolve",
    ...                                 direction="decrease", minimum_worthwhile_effect=5.0))
    >>> d.verdict          # act | act_with_approval | experiment | refuse
    >>> d.rationale        # plain language, numbers bound to the analysis
    >>> d.experiment       # when the evidence cannot settle it: the trial that would

The verdict is a pure function of (outcome, request, policy), so a recorded decision
can be replayed and disputed.
"""

from __future__ import annotations

from causal_decision.contract import (
    Check,
    Decision,
    DecisionPolicy,
    DecisionRequest,
    Evidence,
    ExperimentSpec,
    RiskClass,
    RiskRule,
    Verdict,
)
from causal_decision.policy import decide
from causal_decision.render import explain

__all__ = [
    "Check",
    "Decision",
    "DecisionPolicy",
    "DecisionRequest",
    "Evidence",
    "ExperimentSpec",
    "RiskClass",
    "RiskRule",
    "Verdict",
    "decide",
    "explain",
]
