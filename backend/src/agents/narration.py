"""
Grounded narration: a kernel outcome put into words by an LLM, or by nobody.

The ``explanation_generate`` skill at runtime. An LLM here writes LANGUAGE ONLY.
Every number, interval, identification verdict and decision comes from the kernel
(AGENTS.md rule 2); the model is asked to cite numbers as claim tokens
(``[[claim:effect]]``) and never to write a digit, and whatever it returns is
treated as untrusted text:

1. the prompt is rendered from the versioned template ``prompts/explanation_generate.yaml``
   (:mod:`src.agents.prompt_templates`); the template's id and hash go into the
   provenance record of the call;
2. the model is reached through :func:`src.agents.llm_factory.get_llm` with the
   template's capability, so the provider is whichever is configured (local
   Ollama first) and the call is captured as an ``LLMInvocation``;
3. the reply is scored by :func:`src.agents.grounding_risk.grounding_risk` -- the
   check Guardian rule PR-002 applies, ``causal_core.claims.verify`` against the
   outcome: every number must be a claim of the outcome, every token must exist,
   and causal wording is allowed only for a ``supported`` result;
4. the tokens are rendered with the kernel's own values and the rendered text is
   scored again.

A reply that fails any step is DISCARDED, and the caller's deterministic text is
used instead; :class:`Narration` says which text was used and why. With no LLM
configured the deterministic text is used without an attempt, so the platform
works unchanged without a model, and no test needs one.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any, Literal

import structlog
from causal_core.claims import ClaimError, causal_language_allowed, render
from causal_core.contract import (
    OUTCOME_ADAPTER,
    Abstention,
    ApparatusFailure,
    CausalResult,
)
from pydantic import ValidationError

from src.agents.grounding_risk import GROUNDED, grounding_risk
from src.agents.llm_factory import get_llm, llm_configured
from src.agents.prompt_templates import TemplateError, load_template

if TYPE_CHECKING:
    from pathlib import Path

log = structlog.get_logger(__name__)

SKILL = "explanation_generate"
Persona = Literal["EXECUTIVE", "ANALYST", "OPERATOR", "COMPLIANCE"]

#: A reply longer than this is refused rather than truncated (a cut could split a token).
MAX_REPLY_CHARS = 2000

_THINK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)

#: What each claim id means, for the prompt. Values are shown beside the token so the
#: model can describe direction and size in words; it still has to write the token.
_CLAIM_MEANING = {
    "effect": "the point estimate of the effect",
    "effect.se": "its standard error",
    "effect.ci.lo": "lower end of the confidence interval",
    "effect.ci.hi": "upper end of the confidence interval",
    "ci.level": "the interval's confidence level",
    "n": "rows used",
    "e_value": "E-value of the point estimate",
    "e_value.ci": "E-value of the interval's nearer end",
    "robustness_value": "robustness value (share of residual variance a confounder would need)",
}


@dataclass(frozen=True)
class Narration:
    """What to show, and where it came from."""

    text: str
    source: Literal["llm", "deterministic"]
    reason: str = ""
    template_id: str | None = None
    template_hash: str | None = None
    model: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _timeout_seconds() -> float:
    try:
        return max(1.0, float(os.getenv("LLM_TIMEOUT_SECONDS", "60")))
    except ValueError:
        return 60.0


def _reply_text(reply: Any) -> str:
    content = getattr(reply, "content", reply)
    if isinstance(content, list):  # content blocks
        content = "".join(
            b.get("text", "") if isinstance(b, dict) else str(b) for b in content
        )
    return _THINK.sub("", str(content)).strip()


def _facts(outcome: CausalResult | Abstention, decision: dict[str, Any] | None) -> str:
    """The non-numeric facts of the outcome: names, methods, statuses, reasons."""
    facts: dict[str, Any] = {"kind": outcome.kind}
    if isinstance(outcome, CausalResult):
        facts.update(
            status=outcome.status.value,
            treatment=outcome.query.treatment,
            outcome=outcome.query.outcome,
            estimand=outcome.primary.estimand,
            estimator=outcome.primary.estimator,
            identification_strategy=(
                outcome.certificate.strategy.value if outcome.certificate.strategy else None
            ),
            adjustment_set=list(outcome.certificate.adjustment_set),
            other_estimators=[
                e.estimator for e in outcome.estimates if e.estimator != outcome.primary.estimator
            ],
            warnings=list(outcome.warnings),
        )
    else:
        facts.update(
            reason=outcome.reason.value,
            detail=outcome.detail,
            missing_information=list(outcome.missing_information),
        )
        if outcome.query is not None:
            facts.update(treatment=outcome.query.treatment, outcome=outcome.query.outcome)
    if decision:
        facts["decision"] = decision
    return json.dumps(facts, indent=2, default=str)


def _claims_list(outcome: CausalResult | Abstention) -> str:
    if not isinstance(outcome, CausalResult) or not outcome.claims:
        return "(none -- this explanation must contain no numbers)"
    return "\n".join(
        f"[[claim:{c.id}]] -- {_CLAIM_MEANING.get(c.id, c.path)} (currently {c.text}"
        f"{'%' if c.unit == '%' else ''})"
        for c in outcome.claims
    )


def _assumptions(outcome: CausalResult | Abstention, extra: list[str]) -> str:
    items = list(extra)
    if outcome.certificate is not None:
        items.extend(outcome.certificate.assumptions)
    if isinstance(outcome, CausalResult):
        items.extend(a for a in outcome.primary.assumptions if a not in items)
    return "\n".join(f"- {a}" for a in items) if items else "(none listed by the kernel)"


async def narrate_outcome(
    outcome: CausalResult | Abstention | ApparatusFailure | dict[str, Any],
    *,
    fallback: str,
    session_id: str,
    agent_id: str,
    persona: Persona = "ANALYST",
    decision: dict[str, Any] | None = None,
    assumptions: list[str] | None = None,
    templates_dir: Path | None = None,
) -> Narration:
    """Narrate ``outcome`` with the configured LLM, grounded; else return ``fallback``.

    ``decision`` (optional) adds a verdict's non-numeric facts to the prompt. The reply is
    grounded against ``outcome`` alone, so it may cite no number that is not a claim of
    the analysis.
    """

    def deterministic(reason: str, **extra: Any) -> Narration:
        log.info(
            "narration.deterministic", agent_id=agent_id, session_id=session_id, reason=reason
        )
        return Narration(text=fallback, source="deterministic", reason=reason, **extra)

    if not llm_configured():
        return deterministic("no LLM configured")
    try:
        parsed = (
            OUTCOME_ADAPTER.validate_python(outcome) if isinstance(outcome, dict) else outcome
        )
    except ValidationError as exc:
        return deterministic(f"not a kernel outcome: {exc.error_count()} error(s)")
    if isinstance(parsed, ApparatusFailure):
        return deterministic("apparatus failure: reported verbatim, not narrated")

    try:
        template = load_template(SKILL, templates_dir)
        prompt = template.render(
            result=_facts(parsed, decision),
            claims=_claims_list(parsed),
            assumptions=_assumptions(parsed, assumptions or []),
            causal_language="permitted" if causal_language_allowed(parsed) else "NOT permitted",
            persona=persona,
        )
    except TemplateError as exc:
        return deterministic(f"prompt template: {exc}")
    ids = {"template_id": template.template_id, "template_hash": template.sha256}

    try:
        llm = get_llm(
            capability=template.capability,
            temperature=0.0,
            session_id=session_id,
            agent_id=agent_id,
            prompt_template_id=template.template_id,
            prompt_template_hash=template.sha256,
        )
        model = getattr(llm, "_model_name", None) or getattr(llm, "model", None)
        reply = await asyncio.wait_for(llm.ainvoke(prompt), timeout=_timeout_seconds())
    except Exception as exc:
        return deterministic(f"LLM call failed: {type(exc).__name__}: {exc}"[:300], **ids)

    text = _reply_text(reply)
    if not text:
        return deterministic("LLM reply was empty", model=model, **ids)
    if len(text) > MAX_REPLY_CHARS:
        return deterministic(
            f"LLM reply too long ({len(text)} > {MAX_REPLY_CHARS} characters)", model=model, **ids
        )

    outcome_json = parsed.model_dump(mode="json")
    risk = grounding_risk(text, outcome_json)
    if risk.score != GROUNDED:
        log.warning(
            "narration.rejected", agent_id=agent_id, session_id=session_id, reasons=risk.reasons
        )
        return deterministic(
            "rejected by PR-002 grounding: " + "; ".join(risk.reasons), model=model, **ids
        )
    try:
        rendered = render(text, parsed)
    except ClaimError as exc:
        return deterministic(f"rejected: {exc}", model=model, **ids)
    risk = grounding_risk(rendered, outcome_json)
    if risk.score != GROUNDED:
        return deterministic(
            "rendered text rejected by PR-002 grounding: " + "; ".join(risk.reasons),
            model=model,
            **ids,
        )

    log.info("narration.accepted", agent_id=agent_id, session_id=session_id, model=model)
    return Narration(text=rendered, source="llm", model=model, **ids)
