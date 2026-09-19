"""
Proof-carrying numbers and the causal-language policy.

Every number a result may show in prose is a :class:`~causal_core.contract.Claim`
bound to the field it came from. Narratives -- written by a person or an LLM --
reference them as ``⟦claim:effect⟧`` (ASCII fallback ``[[claim:effect]]``) and
:func:`render` substitutes the value. :func:`verify` checks finished prose
fail-closed:

* every numeric literal must be a faithful rounding of some claim;
* causal wording ("caused", "will reduce", "root cause", ...) is allowed only
  for a ``supported`` result. A refusal, a contested result or a provisional one
  may be described, but not in causal language.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from causal_core.contract import (
    Abstention,
    ApparatusFailure,
    CausalResult,
    Claim,
    ResultStatus,
)

Outcome = CausalResult | Abstention | ApparatusFailure

TOKEN = re.compile(r"⟦claim:([\w.\-]+)⟧|\[\[claim:([\w.\-]+)\]\]")
NUMBER = re.compile(r"(?<![\w.⟦\[:])[-−+]?\d[\d,]*(?:\.\d+)?(?:[eE][-+]?\d+)?")
CAUSAL_LANGUAGE = re.compile(
    r"\b(caus(?:e|es|ed|ing)|leads? to|led to|results? in|resulted in|drives?|drove|"
    r"root[- ]cause|because of|due to|will (?:increase|decrease|reduce|raise|lower|improve|"
    r"cut|boost|prevent)|(?:the )?(?:causal )?(?:effect|impact) of)\b",
    re.IGNORECASE,
)


class ClaimError(KeyError):
    pass


def format_number(x: float, sig: int = 4) -> str:
    if x == 0:
        return "0"
    if float(x).is_integer() and abs(x) < 1e15:
        return str(int(x))
    s = f"{x:.{sig}g}"
    return s.replace("e+0", "e+").replace("e-0", "e-")


def build_claims(
    result_fields: dict[str, tuple[float | None, str, str | None]],
) -> tuple[Claim, ...]:
    """``{claim_id: (value, json_pointer, unit)}`` -> claims, skipping missing values."""
    out = []
    for cid, (value, path, unit) in result_fields.items():
        if value is None or not math.isfinite(value):
            continue
        out.append(
            Claim(id=cid, value=float(value), text=format_number(value), unit=unit, path=path)
        )
    return tuple(out)


def _claims(outcome: Outcome) -> dict[str, Claim]:
    return {c.id: c for c in outcome.claims} if isinstance(outcome, CausalResult) else {}


def render(template: str, outcome: Outcome) -> str:
    """Replace claim tokens with their values. Unknown ids raise :class:`ClaimError`."""
    claims = _claims(outcome)

    def sub(m: re.Match[str]) -> str:
        cid = m.group(1) or m.group(2)
        if cid not in claims:
            raise ClaimError(f"no claim {cid!r} in this outcome (have: {sorted(claims)})")
        c = claims[cid]
        return c.text + ("%" if c.unit == "%" else "")

    return TOKEN.sub(sub, template)


def _decimals(literal: str) -> int:
    mant = re.split(r"[eE]", literal)[0]
    return len(mant.split(".")[1]) if "." in mant else 0


def _matches(literal: str, claim: Claim) -> bool:
    raw = literal.replace("−", "-").replace(",", "")
    try:
        v = float(raw)
    except ValueError:
        return False
    if raw.lstrip("+-") == claim.text.lstrip("+-") and math.copysign(1, v) == math.copysign(
        1, claim.value
    ):
        return True
    if "e" in raw.lower():
        mant = raw.lower().split("e")[0].lstrip("+-")
        sig = len(mant.replace(".", "").lstrip("0")) or 1
        return bool(math.isclose(v, claim.value, rel_tol=0.5 * 10 ** (1 - sig) + 1e-12))
    tol = 0.5 * 10 ** -_decimals(raw) + 1e-12 * max(1.0, abs(claim.value))
    # "0" or "-0" must not stand in for a small but non-zero claim.
    if v == 0 and claim.value != 0 and _decimals(raw) == 0:
        return False
    return bool(abs(v - claim.value) <= tol)


@dataclass
class Verification:
    ok: bool
    unbound_numbers: list[str] = field(default_factory=list)
    unknown_tokens: list[str] = field(default_factory=list)
    causal_language: list[str] = field(default_factory=list)

    def reasons(self) -> list[str]:
        out = []
        if self.unbound_numbers:
            out.append(f"numbers not bound to any claim: {self.unbound_numbers}")
        if self.unknown_tokens:
            out.append(f"unknown claim tokens: {self.unknown_tokens}")
        if self.causal_language:
            out.append(f"causal language not supported by this outcome: {self.causal_language}")
        return out


def causal_language_allowed(outcome: Outcome) -> bool:
    return isinstance(outcome, CausalResult) and outcome.status is ResultStatus.supported


def verify(text: str, outcome: Outcome, allow: tuple[str, ...] = ()) -> Verification:
    """Check prose against an outcome, fail-closed.

    ``allow`` lists literals that are not claims but are legitimately present
    (e.g. a year in a title). Tokens are checked for existence and then removed
    before numbers are scanned, so a rendered or unrendered narrative both work.
    """
    claims = _claims(outcome)
    unknown = [
        m.group(1) or m.group(2)
        for m in TOKEN.finditer(text)
        if (m.group(1) or m.group(2)) not in claims
    ]
    body = TOKEN.sub(" ", text)
    unbound = []
    for m in NUMBER.finditer(body):
        lit = m.group(0)
        if lit in allow:
            continue
        if not any(_matches(lit, c) for c in claims.values()):
            unbound.append(lit)
    causal = (
        []
        if causal_language_allowed(outcome)
        else sorted({m.group(0).lower() for m in CAUSAL_LANGUAGE.finditer(body)})
    )
    return Verification(
        ok=not (unbound or unknown or causal),
        unbound_numbers=unbound,
        unknown_tokens=unknown,
        causal_language=causal,
    )
