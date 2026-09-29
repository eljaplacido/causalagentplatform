"""The analysis endpoints answer from the kernel, and the examples keep their promises.

Each example is simulated from a structural model whose effect it states; the test
runs it through `POST /api/v1/analysis/run` and asks for the stated effect inside the
interval, not for the number the code returned last time. The lessons the page shows
beside each example ("the raw difference is more than twice the effect", ...) are
claims about the data too, so they are checked here as well.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, patch

import pandas as pd
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from src.analysis_examples import example, examples
from src.api.main import app
from src.middleware.rbac import ALL_ROLES, ROUTE_ROLES

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

pytestmark = pytest.mark.asyncio

_USER: dict[str, Any] = {"uid": "analysis-test", "email": "a@test.com", "roles": ["sre"]}
EMAIL_GRAPH = (
    "engagement -> emailed; tenure -> emailed; engagement -> spend; "
    "tenure -> spend; emailed -> spend"
)


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


def _naive(example_id: str) -> tuple[float, float | None]:
    ex = example(example_id)
    assert ex is not None
    df = pd.DataFrame(ex.columns)
    t = df[ex.treatment] == 1
    return float(df[t][ex.outcome].mean() - df[~t][ex.outcome].mean()), ex.true_effect


@pytest.mark.parametrize("ex", [e for e in examples() if e.true_effect is not None], ids=str)
async def test_each_identifiable_example_recovers_its_true_effect(
    client: AsyncClient, ex: Any
) -> None:
    got = await client.get(f"/api/v1/analysis/examples/{ex.id}")
    assert got.status_code == 200
    data = got.json()
    resp = await client.post(
        "/api/v1/analysis/run",
        json={
            "graph": data["graph"],
            "treatment": data["treatment"],
            "outcome": data["outcome"],
            "data": data["columns"],
            "query": {"design": data["design"]},
        },
    )
    assert resp.status_code == 200, resp.text
    outcome = resp.json()["outcome"]
    assert outcome["kind"] == "result"
    assert outcome["status"] == "supported"
    ci = outcome["primary"]["ci"]
    assert ci["lo"] <= ex.true_effect <= ci["hi"], (ex.id, ci, ex.true_effect)
    assert resp.json()["report"].startswith("Estimated effect of")


async def test_the_hidden_confounder_example_is_refused_with_what_would_help(
    client: AsyncClient,
) -> None:
    ex = example("ad-spend-hidden-demand")
    assert ex is not None and ex.true_effect is None
    resp = await client.post(
        "/api/v1/analysis/run",
        json={
            "graph": ex.graph,
            "treatment": ex.treatment,
            "outcome": ex.outcome,
            "data": ex.columns,
        },
    )
    outcome = resp.json()["outcome"]
    assert outcome["kind"] == "refusal"
    assert outcome["reason"] == "NON_IDENTIFIABLE"
    assert any("randomize" in m for m in outcome["missing_information"])


def test_the_lessons_shown_beside_the_examples_are_true_of_their_data() -> None:
    naive, truth = _naive("email-campaign")
    assert truth is not None and naive > 2 * truth  # "more than twice the true effect"
    naive, truth = _naive("discount-churn")
    assert truth is not None and truth < 0 and naive < 0
    assert abs(naive) < abs(truth) / 2  # "understates the drop in churn by more than half"


async def test_check_identifies_from_the_graph_alone(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/v1/analysis/check",
        json={"graph": EMAIL_GRAPH, "treatment": "emailed", "outcome": "spend"},
    )
    body = resp.json()
    assert body["valid"] is True
    assert body["identification"]["identified"] is True
    cert = body["identification"]["certificate"]
    assert sorted(cert["adjustment_set"]) == ["engagement", "tenure"]
    assert body["unmeasured"] == []


async def test_check_treats_a_node_without_a_column_as_unmeasured(client: AsyncClient) -> None:
    """The analysis marks such a node latent; the check must say what the analysis will do."""
    resp = await client.post(
        "/api/v1/analysis/check",
        json={
            "graph": EMAIL_GRAPH,
            "treatment": "emailed",
            "outcome": "spend",
            "columns": ["emailed", "spend", "tenure", "region"],
        },
    )
    body = resp.json()
    assert body["unmeasured"] == ["engagement"]
    assert body["unused_columns"] == ["region"]
    ident = body["identification"]
    assert ident["identified"] is False
    assert any("randomize emailed" in m for m in ident["missing_information"])


async def test_check_reports_a_role_missing_from_the_graph_or_data(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/v1/analysis/check",
        json={"graph": EMAIL_GRAPH, "treatment": "emailed", "outcome": "revenue"},
    )
    assert "not in the graph" in resp.json()["identification"]["refusal"]
    resp = await client.post(
        "/api/v1/analysis/check",
        json={
            "graph": EMAIL_GRAPH,
            "treatment": "emailed",
            "outcome": "spend",
            "columns": ["emailed", "engagement", "tenure"],
        },
    )
    assert "not a column of the data" in resp.json()["identification"]["refusal"]


async def test_check_returns_a_syntax_error_as_a_result(client: AsyncClient) -> None:
    resp = await client.post("/api/v1/analysis/check", json={"graph": "a -> b -> a"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["valid"] is False and body["error"]


async def test_check_lists_unreviewed_edges_and_testable_implications(
    client: AsyncClient,
) -> None:
    resp = await client.post(
        "/api/v1/analysis/check",
        json={"graph": "a -> b [origin=llm_prior, status=proposed]; b -> c"},
    )
    body = resp.json()
    assert body["unreviewed_edges"] == ["a -> b"]
    assert {"a": "a", "b": "c", "given": ["b"]} in body["testable_implications"] or {
        "a": "c",
        "b": "a",
        "given": ["b"],
    } in body["testable_implications"]


async def test_run_records_nothing(client: AsyncClient) -> None:
    ex = example("checkout-ab-test")
    assert ex is not None
    before = (await client.get("/api/v1/decisions")).json()["total"]
    await client.post(
        "/api/v1/analysis/run",
        json={
            "graph": ex.graph,
            "treatment": ex.treatment,
            "outcome": ex.outcome,
            "data": ex.columns,
        },
    )
    assert (await client.get("/api/v1/decisions")).json()["total"] == before


async def test_run_needs_exactly_one_graph_source(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/v1/analysis/run",
        json={"treatment": "t", "outcome": "y", "data": {"t": [0, 1], "y": [1, 2]}},
    )
    assert resp.status_code == 422


async def test_unknown_example_is_404(client: AsyncClient) -> None:
    assert (await client.get("/api/v1/analysis/examples/nope")).status_code == 404


def test_every_analysis_route_is_open_to_every_role() -> None:
    for key in (
        ("POST", "/api/v1/analysis/check"),
        ("POST", "/api/v1/analysis/run"),
        ("GET", "/api/v1/analysis/examples"),
        ("GET", "/api/v1/analysis/examples/{example_id}"),
    ):
        assert ROUTE_ROLES[key] == ALL_ROLES
