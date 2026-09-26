"""A runtime agent renders its prompt from the versioned template in prompts/ (septa-37).

Pins register entry ``prompt-templates-not-loaded``: the SimulationAgent's
explanation_generate prompt is rendered from ``prompts/explanation_generate.yaml``,
its id and hash are recorded on the LLM invocation, and a missing or malformed
template stops the LLM call -- the agent falls back to its deterministic text
instead of sending an ad-hoc prompt.
"""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path
from typing import Any

import pytest

import src.agents.llm_factory as llm_factory
from src.agents.prompt_templates import TemplateError, load_template, prompts_dir
from src.api.provenance import get_provenance_graph
from tests.unit._narration_support import DataSimulationAgent, fake_llm, state

REPO_PROMPTS = Path(__file__).resolve().parents[3] / "prompts"
TEMPLATE = REPO_PROMPTS / "explanation_generate.yaml"
GROUNDED = (
    "The kernel's estimate is [[claim:effect]], "
    "interval [[claim:effect.ci.lo]] to [[claim:effect.ci.hi]]."
)


def _use_fake(monkeypatch: pytest.MonkeyPatch, llm: Any) -> None:
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://ollama.invalid")
    monkeypatch.setattr(llm_factory, "_build_llm", lambda *a, **k: llm)


# ── the loader ────────────────────────────────────────────────────────────────


def test_the_default_directory_is_the_repository_prompts() -> None:
    assert prompts_dir().resolve() == REPO_PROMPTS.resolve()


def test_explanation_generate_loads_with_its_version_and_hash() -> None:
    t = load_template("explanation_generate")
    assert t.skill == "explanation_generate"
    assert t.version == "2.0.0"
    assert t.capability == "causal_reasoning"
    assert t.template_id == "prompts/explanation_generate.yaml@2.0.0"
    assert t.sha256 == hashlib.sha256(TEMPLATE.read_bytes()).hexdigest()
    assert t.variables() == {"result", "claims", "assumptions", "causal_language", "persona"}


def test_render_fills_every_part_and_refuses_holes() -> None:
    t = load_template("explanation_generate")
    values = {v: f"<{v}>" for v in t.variables()}
    prompt = t.render(**values)
    for part in ("[CONTEXT]", "[SKILL]", "[CONSTRAINTS]", "[OUTPUT_FORMAT]"):
        assert part in prompt
    assert "{{" not in prompt and all(f"<{v}>" in prompt for v in values)

    missing = dict(values)
    missing.pop("persona")
    with pytest.raises(TemplateError, match="missing variables \\['persona'\\]"):
        t.render(**missing)
    with pytest.raises(TemplateError, match="unexpected variables \\['surprise'\\]"):
        t.render(**values, surprise="x")


def test_a_value_is_inserted_as_text_not_expanded() -> None:
    t = load_template("explanation_generate")
    values = dict.fromkeys(t.variables(), "plain")
    values["result"] = "{{persona}}"
    assert "{{persona}}" in t.render(**values)


def _write(tmp_path: Path, body: str) -> Path:
    (tmp_path / "explanation_generate.yaml").write_text(body)
    return tmp_path


GOOD = TEMPLATE.read_text()


@pytest.mark.parametrize(
    ("body", "error"),
    [
        (GOOD.replace('version: "2.0.0"', 'version: "two"'), "not MAJOR.MINOR.PATCH"),
        (
            GOOD.replace("skill: explanation_generate", "skill: something_else"),
            "not 'explanation_generate'",
        ),
        (
            GOOD.replace("provider_capability: causal_reasoning", "provider_capability: telepathy"),
            "is not routed",
        ),
        (
            GOOD.replace("constraints: |", "retired_constraints: |"),
            "lacks required fields: \\['constraints'\\]",
        ),
        (GOOD + "\nskill: explanation_generate\n", "duplicate key 'skill'"),
        (GOOD + "\nllm_required: false\n", "deterministic skill"),
        ("- just\n- a list\n", "is not a mapping"),
        ("# only comments\n", "is not a mapping"),
        ("key: [unclosed\n", "not valid YAML"),
    ],
)
def test_a_malformed_template_is_refused(tmp_path: Path, body: str, error: str) -> None:
    with pytest.raises(TemplateError, match=error):
        load_template("explanation_generate", _write(tmp_path, body))


def test_a_missing_template_is_refused(tmp_path: Path) -> None:
    with pytest.raises(TemplateError, match="cannot be read"):
        load_template("explanation_generate", tmp_path)


def test_a_path_is_not_a_skill_name() -> None:
    with pytest.raises(TemplateError, match="not a skill name"):
        load_template("../secrets")


# ── the agent renders from it ─────────────────────────────────────────────────


async def test_the_simulation_agent_prompt_is_the_rendered_template(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    llm = fake_llm(GROUNDED)
    _use_fake(monkeypatch, llm)
    session = "septa-37-rendered"

    result = await DataSimulationAgent().process(state(session))

    assert len(llm.prompts) == 1
    prompt = llm.prompts[0]
    template = load_template("explanation_generate")
    # The fixed text of every part is in the prompt, and nothing was left unfilled.
    for fixed in (
        template.context.strip().splitlines()[0],
        "Explain this kernel outcome.",
        "Do not write any digit.",
        "[OUTPUT_FORMAT]",
    ):
        assert fixed in prompt
    assert "{{" not in prompt
    # ... filled with this outcome: its claim tokens, and permission for causal wording
    # because the kernel called the result supported.
    assert "[[claim:effect]]" in prompt and "[[claim:e_value]]" in prompt
    assert "is permitted for this outcome" in prompt

    invocations = get_provenance_graph(session).llm_invocations
    assert len(invocations) == 1
    assert invocations[0].prompt_template_id == template.template_id
    assert invocations[0].prompt_template_hash == template.sha256
    step = next(s for s in result.reasoning_steps if s.action == "NARRATION")
    assert step.outputs["template_id"] == template.template_id


async def test_a_malformed_template_stops_the_llm_call(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    llm = fake_llm(GROUNDED)
    _use_fake(monkeypatch, llm)
    duplicated = GOOD + "\nskill: explanation_generate\n"
    monkeypatch.setenv("PROMPTS_DIR", str(_write(tmp_path, duplicated)))
    session = "septa-37-malformed"

    result = await DataSimulationAgent().process(state(session))

    assert llm.prompts == []  # no prompt was sent: there is no ad-hoc fallback prompt
    assert get_provenance_graph(session).llm_invocations == []
    assert result.draft_explanation.startswith("Simulation complete.")
    step = next(s for s in result.reasoning_steps if s.action == "NARRATION")
    assert step.outputs["source"] == "deterministic"
    assert "duplicate key 'skill'" in step.outputs["reason"]


async def test_a_missing_template_stops_the_llm_call(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    llm = fake_llm(GROUNDED)
    _use_fake(monkeypatch, llm)
    empty = tmp_path / "prompts"
    empty.mkdir()
    shutil.copy(REPO_PROMPTS / "README.md", empty / "README.md")
    monkeypatch.setenv("PROMPTS_DIR", str(empty))

    result = await DataSimulationAgent().process(state("septa-37-missing"))

    assert llm.prompts == []
    step = next(s for s in result.reasoning_steps if s.action == "NARRATION")
    assert step.outputs["source"] == "deterministic"
    assert "cannot be read" in step.outputs["reason"]
