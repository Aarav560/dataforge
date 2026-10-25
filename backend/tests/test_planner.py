import pytest

from app import planner
from app.config import Settings


def test_heuristic_tetris_plan():
    plan = planner.plan_heuristic("I want to train a Tetris AI")
    assert plan.approach == "reinforcement_learning"
    kinds = [n.kind for n in plan.needs]
    assert kinds == ["dataset", "model", "environment", "video", "paper", "code"]
    assert all("tetris" in q.lower() for n in plan.needs for q in n.queries)
    assert plan.needs[0].source_queries().keys() == {"huggingface", "kaggle", "openml", "github"}


def test_heuristic_supervised():
    plan = planner.plan_heuristic("classify bird species from photos")
    assert plan.approach == "supervised"
    assert not any(n.kind in ("environment", "video") for n in plan.needs)
    assert [n for n in plan.needs if n.kind == "paper"][0].source_queries().keys() == {"arxiv", "semanticscholar"}


async def test_no_key_uses_heuristic():
    plan = await planner.make_plan("Tetris AI", Settings(anthropic_api_key=None))
    assert plan.planner == "heuristic"


async def test_claude_failure_falls_back(monkeypatch):
    async def boom(goal, settings):
        raise RuntimeError("planner request was declined")

    monkeypatch.setattr(planner, "plan_with_claude", boom)
    plan = await planner.make_plan("Tetris AI", Settings(anthropic_api_key="sk-test"))
    assert plan.planner == "heuristic"


async def test_claude_plan_parsed(monkeypatch):
    class Block:
        type = "text"
        text = (
            '{"approach":"imitation_learning","approach_reason":"r","needs":['
            '{"description":"Logged games","kind":"dataset","queries":["tetris games"," "]},'
            '{"description":"bad","kind":"forum","queries":["x"]}]}'
        )

    class Resp:
        stop_reason = "end_turn"
        content = [Block()]

    class FakeMessages:
        async def create(self, **kwargs):
            assert kwargs["fallbacks"] == "default"
            assert kwargs["output_config"]["format"]["type"] == "json_schema"
            return Resp()

    class FakeClient:
        def __init__(self, **kw):
            self.beta = type("B", (), {"messages": FakeMessages()})()

    monkeypatch.setattr(planner.anthropic, "AsyncAnthropic", FakeClient)
    plan = await planner.make_plan("Tetris AI", Settings(anthropic_api_key="sk-test"))
    assert plan.planner == "claude"
    assert plan.approach == "imitation_learning"
    assert [(n.kind, n.queries) for n in plan.needs] == [("dataset", ["tetris games"])]
