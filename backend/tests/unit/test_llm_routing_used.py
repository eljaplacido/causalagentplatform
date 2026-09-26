"""An agent asks get_llm for a capability, and its output depends on the reply (septa-17).

Pins register entry ``llm-routing-no-callers``. The SimulationAgent's draft
explanation is written by whatever model ``get_llm`` routes the template's capability
to -- here LangChain's deterministic fake chat model, standing in for the client
``_build_llm`` would construct -- and is KEPT only when it passes the PR-002
grounding check against the kernel outcome:

* grounded claim tokens are rendered with the kernel's values, and two different
  grounded replies give two different explanations;
* a number the kernel did not produce, an unknown token, causal wording on an
  outcome that does not license it, an empty reply, a failed or slow call: the reply
  is discarded, the deterministic text is kept, and the reason is recorded;
* with no provider configured the model is not asked at all.

Every causal number in these tests comes from the kernel run on data simulated from
a structural model whose effect is ``TRUE_EFFECT``; the tests check the kernel's
interval covers it before checking what the LLM layer did with the number.

The one live test (``llm_live``) calls a local Ollama and runs only with LLM_LIVE=1.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

import pytest
from causal_core import analyze
from causal_core.claims import verify
from causal_core.contract import OUTCOME_ADAPTER, CausalResult, ResultStatus

import src.agents.llm_factory as llm_factory
from src.agents.grounding_risk import grounding_risk
from src.agents.narration import narrate_outcome
from src.api.provenance import get_provenance_graph
from src.skills._kernel import fragment_to_graph
from tests.unit._narration_support import (
    TRUE_EFFECT,
    DataSimulationAgent,
    X,
    Y,
    fake_llm,
    fragment,
    scm_frame,
    state,
)

GROUNDED = (
    "The kernel's estimate is [[claim:effect]], with a [[claim:ci.level]] interval from "
    "[[claim:effect.ci.lo]] to [[claim:effect.ci.hi]], on [[claim:n]] rows."
)
GROUNDED_OTHER = (
    "An E-value of [[claim:e_value]] says how strong a hidden confounder would need to be."
)


def _use_fake(monkeypatch: pytest.MonkeyPatch, llm: Any) -> list[tuple[Any, ...]]:
    """Configure Ollama and hand back the fake instead of a ChatOllama; record the build calls."""
    calls: list[tuple[Any, ...]] = []

    def build(*args: Any, **kwargs: Any) -> Any:
        calls.append(args)
        return llm

    monkeypatch.setenv("OLLAMA_BASE_URL", "http://ollama.invalid")
    monkeypatch.setenv("OLLAMA_CAUSAL_MODEL", "fake-narrator")
    monkeypatch.setattr(llm_factory, "_build_llm", build)
    return calls


def _kernel_outcome() -> CausalResult:
    out = analyze(scm_frame(), fragment_to_graph(fragment()), treatment=X, outcome=Y)
    assert isinstance(out, CausalResult)
    assert out.primary.ci is not None
    assert out.primary.ci.lo <= TRUE_EFFECT <= out.primary.ci.hi  # the kernel found the truth
    return out


def _narration_step(result: Any) -> dict[str, Any]:
    step = next(s for s in result.reasoning_steps if s.action == "NARRATION")
    return dict(step.outputs)


# ── the agent path ────────────────────────────────────────────────────────────


async def test_the_agent_asks_get_llm_for_the_templates_capability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _use_fake(monkeypatch, fake_llm(GROUNDED))
    session = "septa-17-route"

    result = await DataSimulationAgent().process(state(session))

    # _build_llm(provider, model, capability, temperature): routed by capability, local-first.
    assert calls == [("ollama", "fake-narrator", "causal_reasoning", 0.0)]
    sim = result.simulation_results[0]
    assert sim.confidence_interval[0] <= TRUE_EFFECT <= sim.confidence_interval[1]

    step = _narration_step(result)
    assert step["source"] == "llm" and step["model"] == "fake-narrator"
    inv = get_provenance_graph(session).llm_invocations
    assert [(i.agent_id, i.provider.value, i.capability.value, i.model_name) for i in inv] == [
        ("simulation-agent", "ollama", "causal_reasoning", "fake-narrator")
    ]


async def test_a_grounded_reply_is_rendered_with_the_kernels_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_fake(monkeypatch, fake_llm(GROUNDED))

    result = await DataSimulationAgent().process(state("septa-17-grounded"))

    text = result.draft_explanation
    assert "[[claim:" not in text
    outcome = _kernel_outcome()  # same seed, same data: the outcome the agent narrated
    claims = {c.id: c.text for c in outcome.claims}
    assert text == (
        f"The kernel's estimate is {claims['effect']}, with a {claims['ci.level']}% interval "
        f"from {claims['effect.ci.lo']} to {claims['effect.ci.hi']}, on {claims['n']} rows."
    )
    assert verify(text, outcome).ok


async def test_the_explanation_depends_on_the_models_reply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_fake(monkeypatch, fake_llm(GROUNDED))
    first = await DataSimulationAgent().process(state("septa-17-a"))
    _use_fake(monkeypatch, fake_llm(GROUNDED_OTHER))
    second = await DataSimulationAgent().process(state("septa-17-b"))

    assert first.draft_explanation != second.draft_explanation
    assert second.draft_explanation.startswith("An E-value of ")
    # The numbers did not move: they are the kernel's, whatever the model wrote.
    assert first.simulation_results[0].ate_estimate == second.simulation_results[0].ate_estimate


async def test_an_ungrounded_number_is_rejected_and_the_deterministic_text_kept(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_fake(monkeypatch, fake_llm("Deploying this raises conversion by 7.5 points."))

    result = await DataSimulationAgent().process(state("septa-17-ungrounded"))

    assert result.draft_explanation.startswith("Simulation complete. ATE estimate: ")
    assert "7.5" not in result.draft_explanation
    step = _narration_step(result)
    assert step["source"] == "deterministic"
    assert "rejected by PR-002 grounding" in step["reason"]
    assert "numbers not bound to any claim: ['7.5']" in step["reason"]


async def test_an_unknown_claim_token_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    _use_fake(monkeypatch, fake_llm("Uplift was [[claim:uplift]]."))

    result = await DataSimulationAgent().process(state("septa-17-unknown-token"))

    step = _narration_step(result)
    assert step["source"] == "deterministic"
    assert "unknown claim tokens: ['uplift']" in step["reason"]
    assert "[[claim:" not in result.draft_explanation


async def test_a_digit_copied_faithfully_from_the_claims_is_accepted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The gate is grounding, not style: the claim's own text, written out, is bound to it."""
    outcome = _kernel_outcome()
    effect = next(c.text for c in outcome.claims if c.id == "effect")
    _use_fake(monkeypatch, fake_llm(f"The estimate is {effect}."))

    result = await DataSimulationAgent().process(state("septa-17-faithful"))

    assert result.draft_explanation == f"The estimate is {effect}."


async def test_think_blocks_are_dropped_before_grounding(monkeypatch: pytest.MonkeyPatch) -> None:
    _use_fake(monkeypatch, fake_llm("<think>maybe 42?</think>" + GROUNDED_OTHER))

    result = await DataSimulationAgent().process(state("septa-17-think"))

    assert _narration_step(result)["source"] == "llm"
    assert "42" not in result.draft_explanation


# ── narrate_outcome directly: the other refusals ─────────────────────────────


async def test_causal_wording_is_rejected_on_a_contested_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contested = _kernel_outcome().model_copy(update={"status": ResultStatus.contested})
    _use_fake(monkeypatch, fake_llm("The change caused an effect of [[claim:effect]]."))

    n = await narrate_outcome(contested, fallback="FALLBACK", session_id="s", agent_id="a")

    assert (n.source, n.text) == ("deterministic", "FALLBACK")
    assert "causal language not supported by this outcome" in n.reason


async def test_a_refusal_may_be_described_but_not_quantified(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Treatment never varies: the kernel refuses; there are no claims to cite.
    frame = scm_frame()
    frame[X] = 1.0
    refusal = analyze(frame, fragment_to_graph(fragment()), treatment=X, outcome=Y)
    assert refusal.kind == "refusal"

    _use_fake(monkeypatch, fake_llm("The kernel declined to estimate: the treatment never varies."))
    ok = await narrate_outcome(
        refusal.model_dump(mode="json"), fallback="F", session_id="s", agent_id="a"
    )
    assert ok.source == "llm"

    _use_fake(monkeypatch, fake_llm("About 3 in 4 units would have improved."))
    bad = await narrate_outcome(refusal, fallback="F", session_id="s", agent_id="a")
    assert bad.source == "deterministic" and "numbers not bound" in bad.reason


async def test_no_provider_means_no_attempt(monkeypatch: pytest.MonkeyPatch) -> None:
    """The autouse fixture leaves no provider configured: the fallback, and no call."""
    calls: list[Any] = []
    monkeypatch.setattr(llm_factory, "_build_llm", lambda *a, **k: calls.append(a))

    result = await DataSimulationAgent().process(state("septa-17-none"))

    assert calls == []
    assert get_provenance_graph("septa-17-none").llm_invocations == []
    step = _narration_step(result)
    assert (step["source"], step["reason"]) == ("deterministic", "no LLM configured")
    assert result.draft_explanation.startswith("Simulation complete. ATE estimate: ")


async def test_a_failing_call_falls_back_and_is_still_captured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Broken:
        async def ainvoke(self, prompt: Any, **kwargs: Any) -> Any:
            raise ConnectionError("ollama is down")

    _use_fake(monkeypatch, Broken())
    n = await narrate_outcome(
        _kernel_outcome(), fallback="F", session_id="septa-17-broken", agent_id="a"
    )
    assert n.source == "deterministic" and "ConnectionError: ollama is down" in n.reason
    assert len(get_provenance_graph("septa-17-broken").llm_invocations) == 1


async def test_a_slow_call_times_out(monkeypatch: pytest.MonkeyPatch) -> None:
    class Slow:
        async def ainvoke(self, prompt: Any, **kwargs: Any) -> Any:
            await asyncio.sleep(30)

    _use_fake(monkeypatch, Slow())
    monkeypatch.setenv("LLM_TIMEOUT_SECONDS", "1")
    n = await narrate_outcome(_kernel_outcome(), fallback="F", session_id="s", agent_id="a")
    assert n.source == "deterministic" and "TimeoutError" in n.reason


@pytest.mark.parametrize(
    ("reply", "reason"),
    [("   ", "reply was empty"), ("word " * 500, "reply too long")],
)
async def test_empty_and_overlong_replies_are_refused(
    monkeypatch: pytest.MonkeyPatch, reply: str, reason: str
) -> None:
    _use_fake(monkeypatch, fake_llm(reply))
    n = await narrate_outcome(_kernel_outcome(), fallback="F", session_id="s", agent_id="a")
    assert n.source == "deterministic" and reason in n.reason


async def test_an_apparatus_failure_is_not_narrated(monkeypatch: pytest.MonkeyPatch) -> None:
    llm = fake_llm(GROUNDED)
    _use_fake(monkeypatch, llm)
    failure = {
        "kind": "apparatus_failure",
        "stage": "estimate",
        "error_type": "LinAlgError",
        "detail": "singular matrix",
        "provenance": _kernel_outcome().provenance.model_dump(mode="json"),
    }
    n = await narrate_outcome(failure, fallback="F", session_id="s", agent_id="a")
    assert n.source == "deterministic" and "apparatus failure" in n.reason
    assert llm.prompts == []


# ── live: a real local model, only with LLM_LIVE=1 ────────────────────────────


@pytest.mark.llm_live
async def test_live_local_ollama_narration_is_grounded_or_refused() -> None:
    """Against the Ollama at OLLAMA_BASE_URL, with OLLAMA_CAUSAL_MODEL (or OLLAMA_MODEL).

    A live model may or may not follow the template; what must hold either way is that
    nothing ungrounded is kept, and that the call is captured with the real model name.
    """
    if not os.environ.get("OLLAMA_BASE_URL"):
        pytest.fail("LLM_LIVE=1 needs OLLAMA_BASE_URL (and a model in OLLAMA_CAUSAL_MODEL)")
    outcome = _kernel_outcome()
    session = "llm-live"
    n = await narrate_outcome(
        outcome, fallback="FALLBACK", session_id=session, agent_id="live-test", persona="EXECUTIVE"
    )
    print(f"\nlive narration: source={n.source} model={n.model} reason={n.reason!r}\n{n.text}")

    inv = get_provenance_graph(session).llm_invocations
    assert len(inv) == 1 and inv[0].provider.value == "ollama"
    assert inv[0].model_name == n.model
    assert inv[0].prompt_template_id == "prompts/explanation_generate.yaml@2.0.0"
    if n.source == "llm":
        assert grounding_risk(n.text, outcome.model_dump(mode="json")).score == 0.0
        assert verify(n.text, OUTCOME_ADAPTER.validate_python(outcome.model_dump(mode="json"))).ok
    else:
        assert n.text == "FALLBACK"
        assert n.reason.startswith(("rejected", "rendered text rejected", "LLM reply"))
