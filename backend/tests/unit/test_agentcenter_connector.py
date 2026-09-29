"""The agentcenter connector reads the second brain faithfully, and fails soft.

Served by an httpx.MockTransport shaped like agentcenter and the Oracle (the
shapes were read from the running services on 2026-09-29): a hypothesis becomes
an analysis prefill with its data binding judged; a review item a next step; a
radar report's improvement targets one suggestion each. A source that is down is
reported as unreachable while the other still answers; a decision event goes to
`/events/emit` only when emitting is on, and a failure there never raises.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from src.connectors import agentcenter as ac

AC = "http://ac.test"
ORACLE = "http://oracle.test"

H01 = {
    "hyp_id": "H-01",
    "spec": {
        "title": "Route code tasks to the dense writer",
        "statement": "Routing code tasks to the 27B raises the pass rate",
        "arms": {"1": "dense 27B", "0": "MoE 35B"},
        "outcome_label": "HumanEval item passed (0/1)",
        "graph": "arm -> outcome [origin=experiment, status=proposed]",
        "design": "randomized",
        "estimand": "ate",
        "binding": {"builder": "bench_pair", "params": {"treated_run": "T1", "control_run": "C0"}},
        "caveats": ["HumanEval is function completion"],
        "decision": {
            "action": "route code tasks to the dense writer",
            "direction": "increase",
            "minimum_worthwhile_effect": 0.05,
        },
    },
    "preflight": {"predicted": ["UNAPPROVED_ASSUMPTION until you approve 1 edge(s)"]},
}
H03 = {
    "hyp_id": "H-03",
    "spec": {
        "title": "Window, not timeout",
        "graph": "arm -> outcome; timeout -> outcome",
        "design": "randomized",
        "binding": {"builder": "none", "missing": "Run CTL-1: a 2x2 of window by timeout"},
    },
}
H02 = {
    "hyp_id": "H-02",
    "spec": {
        "title": "Keep the quantized KV cache",
        "arms": {"1": "q8_0 KV cache"},
        "graph": "arm -> outcome",
        "design": "observational",
        "binding": {"builder": "kv_screen"},
    },
}
WISDOM = {
    "hypotheses": [
        {
            "hyp_id": "H-01",
            "project": "agentcenter",
            "zone": "probe first",
            "why": "cheap to try",
            "rung_name": "simulated",
        },
    ]
}
REVIEW = {
    "queue": [
        {
            "kind": "alarm",
            "rank": 0,
            "id": "x1",
            "what": "Void the misattributed runs",
            "about": "ifeval",
            "unlocks": "a scorecard you can trust",
            "how": ["check", "void"],
            "time": "10 min",
            "project": "agentcenter",
        },
    ]
}
RADAR_MD = """# Learning Radar

## Project pulse

**causalplatform**
*   **Progress:** merged the train.

## Improvement targets

**RCCAEF**
1.  **Integrate `sealvera-go` for cryptographic audit logs.** Motivated by a signal.
2.  **Investigate the logging gap.** The gate failed to record a failure.

**causalplatform**
1.  **Run the analysis page against a real use case.** Motivated by the new UI.
"""
ITEMS = {
    "T1": [{"item_id": "a", "correct": True}, {"item_id": "b", "correct": False}],
    "C0": [{"item_id": "a", "correct": False}, {"item_id": "b", "correct": False}],
}


def handler(
    *, oracle_up: bool = True, emitted: list[dict[str, Any]] | None = None, emit_status: int = 200
) -> httpx.MockTransport:
    def respond(req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        if url.startswith(ORACLE):
            if not oracle_up:
                raise httpx.ConnectError("refused", request=req)
            path = req.url.path
            body = {
                "/api/hypotheses": {"hypotheses": [H01, H02, H03]},
                "/api/wisdom": WISDOM,
                "/api/review": REVIEW,
            }.get(path)
            return httpx.Response(200, json=body) if body is not None else httpx.Response(404)
        path = req.url.path
        if path == "/insights/radar":
            return httpx.Response(200, json=[{"date": "2026-09-27"}, {"date": "2026-09-25"}])
        if path == "/insights/radar/2026-09-27":
            return httpx.Response(200, json={"date": "2026-09-27", "content": RADAR_MD})
        if path.startswith("/benchmarks/results/"):
            run = path.split("/")[3]
            return httpx.Response(200, json=ITEMS.get(run, []))
        if path == "/events/emit" and req.method == "POST":
            if emitted is not None:
                emitted.append(json.loads(req.content))
            return httpx.Response(emit_status, json={"event_id": "e1"})
        return httpx.Response(404)

    return httpx.MockTransport(respond)


@pytest.fixture
def cfg() -> ac.Config:
    return ac.Config(agentcenter_url=AC, oracle_url=ORACLE, emit=True, project="causalplatform")


@pytest.fixture(autouse=True)
def _reset_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ac, "_transport", handler())


def test_config_is_off_until_agentcenter_is_named(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("AGENTCENTER_URL", "ORACLE_URL", "AGENTCENTER_EMIT", "AGENTCENTER_PROJECT"):
        monkeypatch.delenv(key, raising=False)
    off = ac.config()
    assert not off.enabled and off.oracle_url is None and not off.emit
    monkeypatch.setenv("AGENTCENTER_URL", "http://127.0.0.1:8765/")
    on = ac.config()
    assert on.agentcenter_url == "http://127.0.0.1:8765"
    assert on.oracle_url == "http://127.0.0.1:8767"
    assert not on.emit


@pytest.mark.asyncio
async def test_disabled_returns_nothing_and_calls_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(req: httpx.Request) -> httpx.Response:
        raise AssertionError("no request may be made while disabled")

    monkeypatch.setattr(ac, "_transport", httpx.MockTransport(boom))
    got = await ac.suggestions(ac.Config(None, None, emit=True, project="p"))
    assert got.enabled is False and got.suggestions == [] and got.sources == []


@pytest.mark.asyncio
async def test_a_hypothesis_becomes_a_prefilled_analysis(cfg: ac.Config) -> None:
    got = await ac.suggestions(cfg)
    h01 = next(s for s in got.suggestions if s.id == "oracle:H-01")
    assert h01.kind == "experiment" and h01.source == "oracle.hypothesis"
    assert (h01.zone, h01.project, h01.why, h01.rung) == (
        "probe first",
        "agentcenter",
        "cheap to try",
        "simulated",
    )
    assert h01.study is not None
    assert h01.study.graph == "arm -> outcome [origin=experiment, status=proposed]"
    assert (h01.study.treatment, h01.study.outcome, h01.study.design) == (
        "arm",
        "outcome",
        "randomized",
    )
    assert h01.study.action == "route code tasks to the dense writer"
    assert h01.study.minimum_worthwhile_effect == 0.05
    assert h01.study.arms == {"1": "dense 27B", "0": "MoE 35B"}
    assert h01.data is not None and h01.data.fetchable and h01.data.builder == "bench_pair"
    assert "HumanEval is function completion" in h01.detail
    assert any(d.startswith("preflight: UNAPPROVED_ASSUMPTION") for d in h01.detail)


@pytest.mark.asyncio
async def test_bindings_this_platform_cannot_fetch_say_what_to_do(cfg: ac.Config) -> None:
    got = {s.id: s for s in (await ac.suggestions(cfg)).suggestions}
    none = got["oracle:H-03"].data
    assert none is not None and not none.fetchable and "CTL-1" in none.note
    other = got["oracle:H-02"].data
    assert other is not None and not other.fetchable
    assert "kv_screen" in other.note and "`arm`" in other.note and "q8_0" in other.note
    # A hypothesis without wisdom still comes through, with the statement as why.
    assert got["oracle:H-02"].zone is None


@pytest.mark.asyncio
async def test_review_items_and_radar_targets(cfg: ac.Config) -> None:
    got = await ac.suggestions(cfg)
    step = next(s for s in got.suggestions if s.kind == "next_step")
    assert (step.title, step.detail, step.time, step.project) == (
        "Void the misattributed runs",
        ["check", "void"],
        "10 min",
        "agentcenter",
    )
    targets = [s for s in got.suggestions if s.kind == "improvement"]
    assert [(t.project, t.title) for t in targets] == [
        ("RCCAEF", "Integrate `sealvera-go` for cryptographic audit logs"),
        ("RCCAEF", "Investigate the logging gap"),
        ("causalplatform", "Run the analysis page against a real use case"),
    ]
    assert targets[1].why == "The gate failed to record a failure."
    assert {s.name: (s.reachable, s.count) for s in got.sources} == {
        "oracle": (True, 4),
        "agentcenter": (True, 3),
    }


def test_a_report_without_targets_gives_none() -> None:
    assert ac.radar_targets("# Radar\n\n## Project pulse\n\n**x**\n1.  **y.** z\n", "d") == []


@pytest.mark.asyncio
async def test_one_source_down_does_not_hide_the_other(
    cfg: ac.Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ac, "_transport", handler(oracle_up=False))
    got = await ac.suggestions(cfg)
    status = {s.name: s for s in got.sources}
    assert not status["oracle"].reachable and "ConnectError" in (status["oracle"].error or "")
    assert status["agentcenter"].reachable
    assert {s.kind for s in got.suggestions} == {"improvement"}


@pytest.mark.asyncio
async def test_bench_pair_data_is_built_as_the_oracle_builds_it(cfg: ac.Config) -> None:
    bound = await ac.bound_data("H-01", cfg)
    assert bound.columns == {
        "item": ["a", "b", "a", "b"],
        "arm": [1, 1, 0, 0],
        "outcome": [1, 0, 0, 0],
    }
    assert bound.sources == ["agentcenter:runs:T1 (2 items)", "agentcenter:runs:C0 (2 items)"]


@pytest.mark.asyncio
async def test_unfetchable_and_unknown_hypotheses(cfg: ac.Config) -> None:
    with pytest.raises(ac.BindingUnavailableError, match="CTL-1"):
        await ac.bound_data("H-03", cfg)
    with pytest.raises(ac.BindingUnavailableError, match="kv_screen"):
        await ac.bound_data("H-02", cfg)
    with pytest.raises(LookupError):
        await ac.bound_data("H-99", cfg)


@pytest.mark.asyncio
async def test_a_decision_is_emitted_only_when_emitting_is_on(
    cfg: ac.Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent: list[dict[str, Any]] = []
    monkeypatch.setattr(ac, "_transport", handler(emitted=sent))
    assert await ac.emit_decision({"verdict": "act", "origin": "oracle:H-01"}, cfg) is True
    assert sent == [
        {
            "kind": "causalplatform.decision.recorded",
            "project": "causalplatform",
            "agent": "causalplatform",
            "payload": {"verdict": "act", "origin": "oracle:H-01"},
        }
    ]
    off = ac.Config(AC, ORACLE, emit=False, project="causalplatform")
    assert await ac.emit_decision({"verdict": "act"}, off) is False
    assert len(sent) == 1


@pytest.mark.asyncio
async def test_a_failed_emit_is_swallowed(cfg: ac.Config, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ac, "_transport", handler(emit_status=500))
    assert await ac.emit_decision({"verdict": "act"}, cfg) is False
