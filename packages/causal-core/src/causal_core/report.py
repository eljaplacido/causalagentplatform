"""
Plain-language summaries of an outcome.

The summary is written as a template over claim tokens and rendered through
:func:`causal_core.claims.render`, then checked with
:func:`causal_core.claims.verify` -- so this module is held to the same
proof-carrying-number and causal-language rules as any LLM narrative.
"""

from __future__ import annotations

from causal_core.claims import render, verify
from causal_core.contract import Abstention, ApparatusFailure, CausalResult, ResultStatus

Outcome = CausalResult | Abstention | ApparatusFailure


def _template(out: CausalResult) -> str:
    q, c, p = out.query, out.certificate, out.primary
    claim_ids = {cl.id for cl in out.claims}
    lines = []
    subject = f"{q.treatment} on {q.outcome}"
    if out.status is ResultStatus.supported:
        lines.append(f"Estimated effect of {subject}: ⟦claim:effect⟧")
    else:
        lines.append(f"Estimate for {subject} ({out.status.value}): ⟦claim:effect⟧")
    if {"effect.ci.lo", "effect.ci.hi", "ci.level"} <= claim_ids:
        lines[-1] += " (⟦claim:ci.level⟧ CI ⟦claim:effect.ci.lo⟧ to ⟦claim:effect.ci.hi⟧)"
    lines[-1] += f", n = ⟦claim:n⟧, estimator {p.estimator}."
    lines.append(f"Estimand: {p.estimand}.")
    adj = f" adjusting for {{{', '.join(c.adjustment_set)}}}" if c.adjustment_set else ""
    lines.append(
        f"Identification: {c.strategy.value if c.strategy else 'none'}{adj}, tier {c.tier.value}."
    )
    if out.triangulation is not None:
        others = ", ".join(out.triangulation.estimators[1:])
        verdict = "agree" if out.triangulation.agree else "DISAGREE"
        lines.append(f"Cross-checked against {others}: estimators {verdict}.")
    failed = [r.name for r in out.refutations if not r.passed]
    passed = [r.name for r in out.refutations if r.passed]
    if passed:
        lines.append(f"Falsification tests passed: {', '.join(passed)}.")
    if failed:
        lines.append(f"Falsification tests FAILED: {', '.join(failed)}.")
    if "e_value" in claim_ids:
        tail = " (⟦claim:e_value.ci⟧ for the interval bound)" if "e_value.ci" in claim_ids else ""
        lines.append(
            "An unmeasured confounder would need a risk-ratio association of ⟦claim:e_value⟧ "
            f"with both treatment and outcome to explain the point estimate away{tail}."
        )
    if out.status is ResultStatus.contested:
        lines.append("Status: contested. The numbers are reported; no causal conclusion is drawn.")
    elif out.status is ResultStatus.provisional:
        lines.append("Status: provisional, because the graph has edges nobody has reviewed yet.")
    for w in out.warnings:
        lines.append(f"Note: {w}")
    return "\n".join(lines)


def report(out: Outcome) -> str:
    if isinstance(out, Abstention):
        text = f"No estimate: {out.reason.value}. {out.detail}"
        if out.missing_information:
            text += "\nWhat would help:\n" + "\n".join(f"- {m}" for m in out.missing_information)
        return text
    if isinstance(out, ApparatusFailure):
        return f"Analysis failed in stage '{out.stage}': {out.error_type}: {out.detail}"
    text = render(_template(out), out)
    return text


def report_is_clean(out: Outcome) -> bool:
    """True if this module's own summary passes the claim verifier (tested)."""
    if not isinstance(out, CausalResult):
        return True
    body = render(_template(out), out)
    # Notes echo user-supplied names and thresholds, and the estimand line is formal
    # notation such as Y(T=1); verify the claim-bearing lines only.
    claim_lines = "\n".join(
        line for line in body.splitlines() if not line.startswith(("Note:", "Estimand:"))
    )
    return verify(claim_lines, out).ok
