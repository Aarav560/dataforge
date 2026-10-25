import pytest

from app.config import Settings
from app.synthetic import advisor


@pytest.mark.parametrize(
    "goal,recommended,template,scaffold",
    [
        ("I want to train a Tetris AI", True, "tetris_synthetic", False),
        ("Solve arithmetic problems", True, "arithmetic_synthetic", False),
        ("Predict a double pendulum's motion", True, None, True),
        ("Classify bird photos", False, None, False),
    ],
)
def test_rules(goal, recommended, template, scaffold):
    a = advisor.advise_rules(goal)
    assert a.recommended is recommended
    assert a.template == template
    assert bool(a.scaffold) is scaffold


def test_scaffold_compiles():
    compile(advisor.scaffold_script("snake game", "game"), "scaffold.py", "exec")


async def test_claude_failure_falls_back(monkeypatch):
    async def boom(goal, settings):
        raise RuntimeError("advisor stopped: refusal")

    monkeypatch.setattr(advisor, "advise_claude", boom)
    a = await advisor.advise("Tetris AI", Settings(anthropic_api_key="sk-test"))
    assert a.source == "rules" and a.template == "tetris_synthetic"
