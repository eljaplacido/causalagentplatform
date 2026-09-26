"""
Load and render the versioned prompt templates in ``prompts/`` (AGENTS.md §6.3).

A template is a YAML mapping with the four parts of the prompt architecture --
``context``, ``skill_prompt``, ``constraints``, ``output_format`` -- plus
``version``, ``skill`` and ``provider_capability``. :func:`load_template` reads
one and refuses anything it cannot vouch for, raising :class:`TemplateError`:

* a file that is missing, is not a mapping, or repeats a key (``yaml.safe_load``
  keeps the last duplicate silently, which once turned ten templates' ``skill``
  into their instruction text);
* a missing or empty required part, a ``version`` that is not ``MAJOR.MINOR.PATCH``,
  a ``skill`` that is not the file's name, a ``provider_capability`` the LLM
  factory does not route, or a template marked ``llm_required: false``.

:meth:`PromptTemplate.render` fills ``{{name}}`` variables from the agent's inputs
and refuses a missing or an unexpected variable, so a prompt is never sent with a
hole in it. Substitution is one pass: a value that itself contains ``{{...}}`` is
inserted as text, not expanded.

The template's identity -- ``prompts/<skill>.yaml@<version>`` and the sha256 of
the file's bytes -- goes into the provenance record of every LLM call made with it
(``llm_factory.get_llm(prompt_template_id=..., prompt_template_hash=...)``).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, get_args

import yaml

from src.agents.llm_factory import Capability

REQUIRED_PARTS = ("context", "skill_prompt", "constraints", "output_format")
_VERSION = re.compile(r"^\d+\.\d+\.\d+$")
_VARIABLE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")


class TemplateError(ValueError):
    """A prompt template is missing, malformed, or rendered with the wrong inputs."""


def _reject_duplicate_keys(node: Any, where: str) -> None:
    """Walk a composed YAML node tree; a mapping that repeats a key raises."""
    if isinstance(node, yaml.MappingNode):
        seen: set[str] = set()
        for key_node, value_node in node.value:
            key = str(key_node.value)
            if key in seen:
                raise TemplateError(
                    f"{where}: duplicate key {key!r} at line {key_node.start_mark.line + 1}"
                )
            seen.add(key)
            _reject_duplicate_keys(value_node, where)
    elif isinstance(node, yaml.SequenceNode):
        for item in node.value:
            _reject_duplicate_keys(item, where)


def prompts_dir() -> Path:
    """``PROMPTS_DIR`` if set; else ``prompts/`` at the repo root, or beside ``src/`` (image)."""
    configured = os.getenv("PROMPTS_DIR")
    if configured:
        return Path(configured)
    here = Path(__file__).resolve()
    for root in (here.parents[3], here.parents[2]):  # checkout: repo/, image: /app/
        candidate = root / "prompts"
        if candidate.is_dir():
            return candidate
    return here.parents[3] / "prompts"


@dataclass(frozen=True)
class PromptTemplate:
    """One validated template from ``prompts/``."""

    skill: str
    version: str
    capability: Capability
    context: str
    skill_prompt: str
    constraints: str
    output_format: Any
    sha256: str

    @property
    def template_id(self) -> str:
        """``prompts/<skill>.yaml@<version>``, as provenance records it."""
        return f"prompts/{self.skill}.yaml@{self.version}"

    def variables(self) -> set[str]:
        """The ``{{name}}`` variables the template needs."""
        found: set[str] = set()
        for part in (self.context, self.skill_prompt, self.constraints):
            found.update(_VARIABLE.findall(part))
        return found

    def render(self, **values: str) -> str:
        """The four-part prompt with every variable filled; a missing or unknown one raises."""
        needed = self.variables()
        missing = sorted(needed - values.keys())
        extra = sorted(values.keys() - needed)
        if missing or extra:
            raise TemplateError(
                f"{self.template_id}: missing variables {missing}, unexpected variables {extra}"
            )

        def fill(text: str) -> str:
            return _VARIABLE.sub(lambda m: str(values[m.group(1)]), text).strip()

        output_format = (
            self.output_format
            if isinstance(self.output_format, str)
            else json.dumps(self.output_format, indent=2, sort_keys=True)
        )
        return "\n\n".join(
            [
                f"[CONTEXT]\n{fill(self.context)}",
                f"[SKILL]\n{fill(self.skill_prompt)}",
                f"[CONSTRAINTS]\n{fill(self.constraints)}",
                f"[OUTPUT_FORMAT]\n{output_format.strip()}",
            ]
        )


def load_template(skill: str, directory: Path | None = None) -> PromptTemplate:
    """Read and validate ``<directory>/<skill>.yaml`` (default :func:`prompts_dir`)."""
    if not re.fullmatch(r"[a-z][a-z0-9_]*", skill):
        raise TemplateError(f"not a skill name: {skill!r}")
    path = (directory or prompts_dir()) / f"{skill}.yaml"
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise TemplateError(f"prompt template {path.name} cannot be read: {exc.strerror}") from exc
    try:
        _reject_duplicate_keys(yaml.compose(raw), path.name)
        doc = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise TemplateError(f"{path.name} is not valid YAML: {exc}") from exc
    if not isinstance(doc, dict):
        raise TemplateError(f"{path.name} is not a mapping")

    if doc.get("llm_required") is False:
        raise TemplateError(f"{path.name} is a deterministic skill (llm_required: false)")
    required = ("version", "skill", "provider_capability", *REQUIRED_PARTS)
    missing = [k for k in required if not doc.get(k)]
    if missing:
        raise TemplateError(f"{path.name} lacks required fields: {missing}")
    version = str(doc["version"])
    if not _VERSION.match(version):
        raise TemplateError(f"{path.name}: version {version!r} is not MAJOR.MINOR.PATCH")
    if doc["skill"] != skill:
        raise TemplateError(f"{path.name}: skill is {doc['skill']!r}, not {skill!r}")
    capability = doc["provider_capability"]
    if capability not in get_args(Capability):
        raise TemplateError(f"{path.name}: provider_capability {capability!r} is not routed")
    for part in ("context", "skill_prompt", "constraints"):
        if not isinstance(doc[part], str):
            raise TemplateError(f"{path.name}: {part} must be text")

    return PromptTemplate(
        skill=skill,
        version=version,
        capability=capability,
        context=doc["context"],
        skill_prompt=doc["skill_prompt"],
        constraints=doc["constraints"],
        output_format=doc["output_format"],
        sha256=hashlib.sha256(raw).hexdigest(),
    )
