"""`POST /api/v1/decisions` with `narrate: true`: a grounded account, or the explanation.

The second caller of the LLM layer (src/agents/narration.py). The verdict, the
analysis and the ledger entry are exactly what they are without it; the narrative is
written after the ledger write, never sealed into it, and kept only when every number
in it is a claim of the analysis. The model is LangChain's deterministic fake; the
data come from a structural model whose effect is set here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, patch

import numpy as np
import pytest
import pytest_asyncio
from causal_core.claims import verify
from causal_core.contract import OUTCOME_ADAPTER
from httpx import ASGITransport, AsyncClient

import src.agents.llm_factory as llm_factory
from src.api.main import app
from tests.unit._narration_support import fake_llm

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

pytestmark = pytest.mark.asyncio

_USER: dict[str, Any] = {"uid": "narrative-test", "email": "n@test.com", "roles": ["sre"]}
TRUE_EFFECT = -10.0


def body(**over: Any) -> dict[str, Any]:
    rng = np.random.default_rng(5)
    n = 3000
    load = rng.normal(0, 1, n)
    playbook = rng.binomial(1, 1 / (1 + np.exp(-0.9 * load))).astype(float)
    minutes = 60 + 8.0 * load + TRUE_EFFECT * playbook + rng.normal(0, 10, n)
    payload: dict[str, Any] = {
        "action": "apply remediation playbook X",
        "graph": "load -> playbook; load -> minutes; playbook -> minutes",
        "treatment": "playbook",
        "outcome": "minutes",
        "direction": "decrease",
        "data": {"load": load.tolist(), "playbook": playbook.tolist(), "minutes": minutes.tolist()},
        "risk_class": "low",
        "minimum_worthwhile_effect": 2.0,
    }
    payload.update(over)
    return payload


@pytest_asyncio.fixture
async def client(db_engine: object) -> AsyncGenerator[AsyncClient, None]:
    with patch(
        "src.middleware.auth._verify_firebase_token", new_callable=AsyncMock, return_value=_USER
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={"Authorization": "Bearer test-token"},
        ) as ac:
            yield ac


def _use_fake(monkeypatch: pytest.MonkeyPatch, reply: str) -> None:
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://ollama.invalid")
    monkeypatch.setattr(llm_factory, "_build_llm", lambda *a, **k: fake_llm(reply))


def _truth_covered(out: dict[str, Any]) -> None:
    ci = out["outcome"]["primary"]["ci"]
    assert ci["lo"] <= TRUE_EFFECT <= ci["hi"], "premise: the kernel found the set effect"


async def test_without_narrate_there_is_no_narrative(client: AsyncClient) -> None:
    res = await client.post("/api/v1/decisions", json=body())
    assert res.status_code == 200, res.text
    assert res.json()["narrative"] is None


async def test_with_no_llm_the_narrative_is_the_explanation(client: AsyncClient) -> None:
    res = await client.post("/api/v1/decisions", json=body(narrate=True))
    assert res.status_code == 200, res.text
    out = res.json()
    assert out["narrative"]["source"] == "deterministic"
    assert out["narrative"]["reason"] == "no LLM configured"
    assert out["narrative"]["text"] == out["explanation"]


async def test_a_grounded_reply_is_returned_and_never_sealed(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use_fake(
        monkeypatch,
        "The verdict is to act: the playbook's estimated change in minutes is [[claim:effect]] "
        "(interval [[claim:effect.ci.lo]] to [[claim:effect.ci.hi]]).",
    )
    res = await client.post("/api/v1/decisions", json=body(narrate=True, persona="OPERATOR"))
    assert res.status_code == 200, res.text
    out = res.json()
    _truth_covered(out)

    narrative = out["narrative"]
    assert narrative["source"] == "llm", narrative
    assert narrative["template_id"] == "prompts/explanation_generate.yaml@2.0.0"
    assert "[[claim:" not in narrative["text"]
    assert verify(narrative["text"], OUTCOME_ADAPTER.validate_python(out["outcome"])).ok

    listed = await client.get("/api/v1/decisions")
    assert listed.status_code == 200, listed.text
    assert narrative["text"] not in listed.text  # the ledger holds the verdict, not the prose


async def test_an_ungrounded_reply_gives_the_explanation(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use_fake(monkeypatch, "Acting will save 45 minutes per incident.")
    res = await client.post("/api/v1/decisions", json=body(narrate=True))
    assert res.status_code == 200, res.text
    out = res.json()
    _truth_covered(out)
    assert out["narrative"]["source"] == "deterministic"
    assert out["narrative"]["text"] == out["explanation"]
    assert "numbers not bound to any claim: ['45']" in out["narrative"]["reason"]
