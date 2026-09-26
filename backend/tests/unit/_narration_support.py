"""Shared fixtures for the narration tests: a known-effect SCM, and fake chat models.

The data come from a structural model whose effect is set here (``TRUE_EFFECT``), so a
test can check the kernel's number against the truth before checking what the LLM
layer did with it. The chat models are LangChain's deterministic fakes: no network.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import numpy as np
import pandas as pd
from langchain_core.language_models.fake_chat_models import FakeListChatModel

from src.agents.simulation import SimulationAgent
from src.semantic_layer.models import CynefinDomain, EpistemicState

TRUE_EFFECT = 2.0

Z, X, Y = str(uuid4()), str(uuid4()), str(uuid4())


def scm_frame(n: int = 2000, seed: int = 7) -> pd.DataFrame:
    """Z confounds X and Y; X's effect on Y is TRUE_EFFECT."""
    rng = np.random.default_rng(seed)
    z = rng.normal(size=n)
    x = (0.8 * z + rng.normal(size=n) > 0).astype(float)
    y = TRUE_EFFECT * x + 1.5 * z + rng.normal(size=n)
    return pd.DataFrame({Z: z, X: x, Y: y})


def fragment() -> dict[str, Any]:
    def edge(a: str, at: str, b: str, bt: str) -> dict[str, Any]:
        return {
            "source": {"id": a, "object_type": at},
            "target": {"id": b, "object_type": bt},
            "edge_type": "CAUSES",
            "confidence": 0.9,
        }

    return {
        "id": str(uuid4()),
        "approval_status": "APPROVED",
        "edges": [
            edge(X, "Treatment", Y, "Outcome"),
            edge(Z, "Confounder", X, "Treatment"),
            edge(Z, "Confounder", Y, "Outcome"),
        ],
    }


class DataSimulationAgent(SimulationAgent):
    """The SimulationAgent with its fragment and data supplied instead of read from the DB."""

    def __init__(self, data: pd.DataFrame | None = None) -> None:
        super().__init__()
        self._fragment = fragment()
        self._supplied = scm_frame() if data is None else data

    async def process(self, state: EpistemicState, data: Any | None = None) -> EpistemicState:
        return await super().process(state, self._supplied if data is None else data)

    async def _skill_semantic_layer_read(self, inputs: dict[str, Any]) -> dict[str, Any]:
        return {"fragment": self._fragment}


def state(session_id: str = "narration-session") -> EpistemicState:
    return EpistemicState(
        session_id=session_id,
        agent_id="test-simulation-agent",
        cynefin_domain=CynefinDomain.COMPLICATED,
        active_graph_fragment_id=str(uuid4()),
        draft_explanation="narration test",
    )


class RecordingFakeLLM(FakeListChatModel):
    """FakeListChatModel that also keeps the prompts it was sent."""

    prompts: list[str] = []

    async def ainvoke(self, input: Any, *args: Any, **kwargs: Any) -> Any:  # noqa: A002
        self.prompts.append(str(input))
        return await super().ainvoke(input, *args, **kwargs)


def fake_llm(*replies: str) -> RecordingFakeLLM:
    return RecordingFakeLLM(responses=list(replies), prompts=[])
