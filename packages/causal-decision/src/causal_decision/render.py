"""
Turn a decision into something a person reads, with the numbers still bound to the analysis.

``explain`` renders the rationale's claim tokens against the causal outcome that produced
it, so every figure in the text is one the kernel computed. It then verifies its own
output: a number that is not a claim, or causal wording the evidence does not support,
is a bug here, not a stylistic matter.
"""

from __future__ import annotations

from causal_core.claims import render, verify

from causal_decision.contract import CausalOutcome, Decision, Verdict

_NEXT = {
    Verdict.act: "Proceed. Record the outcome and compare it with this estimate.",
    Verdict.act_with_approval: "Send this to an approver together with the evidence below.",
    Verdict.experiment: "Run the experiment below, then ask again.",
    Verdict.refuse: "Do nothing on this evidence.",
}


def explain(decision: Decision, outcome: CausalOutcome, strict: bool = True) -> str:
    """Plain-language decision. With ``strict``, refuses to emit unverifiable prose."""
    text = render(decision.rationale, outcome)
    head = decision.verdict.value.upper().replace("_", " ")
    lines = [f"{head}: {text}", "", _NEXT[decision.verdict]]
    if decision.checks:
        lines += ["", "Checks:"]
        lines += [
            f"  [{'ok ' if c.passed else 'no '}] {c.name} -- {c.detail}" for c in decision.checks
        ]
    spec = decision.experiment
    if spec is not None:
        size = (
            f"{spec.units_per_arm} {spec.unit}s per arm"
            if spec.units_per_arm
            else "size unknown (no outcome standard deviation was reported)"
        )
        days = f", about {spec.estimated_days} days" if spec.estimated_days else ""
        lines += [
            "",
            "Experiment that would settle it:",
            f"  {spec.question}",
            f"  Randomize {spec.unit}s, {spec.arms[0]} vs {spec.arms[1]}, {size}{days}.",
            *[f"  Guardrail: {g}" for g in spec.guardrails],
            f"  {spec.note}",
        ]
    if strict:
        # The rationale's own numbers must all be claims of the analysis. The request's
        # thresholds and the experiment's arithmetic are this layer's, not the kernel's,
        # so they are declared rather than smuggled in.
        allowed = _declared_numbers(decision)
        report = verify(_without_quotes(text, decision), outcome, allow=allowed)
        if not report.ok:
            raise ValueError(f"decision rationale failed verification: {report.reasons()}")
    return "\n".join(lines)


def _declared_numbers(decision: Decision) -> tuple[str, ...]:
    r = decision.request
    ev = decision.evidence
    out = {f"{r.minimum_worthwhile_effect:.4g}", f"{r.harm_threshold:.4g}"}
    if ev.benefit_ci:
        out |= {f"{ev.benefit_ci[0]:.4g}", f"{ev.benefit_ci[1]:.4g}"}
    return tuple(out)


def _without_quotes(text: str, decision: Decision) -> str:
    """The rationale with the request's own free text taken out before it is checked.

    The rationale quotes the requester ("Act: cut price 30%."); a digit or a causal verb
    in that quote is theirs, not a claim of the analysis, so it is neither bound nor
    refused. Only the quoted text is removed: the same number anywhere else is checked.
    """
    r = decision.request
    quoted = [q for q in (r.action, r.population, r.horizon) if q and len(q.strip()) >= 3]
    for q in sorted(quoted, key=len, reverse=True):
        text = text.replace(q, " ")
    return text
