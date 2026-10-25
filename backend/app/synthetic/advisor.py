"""Decide whether simulating or generating data beats collecting it.

Keyword rules always run; Claude refines the advice when a key is set.
"""

import json
import logging
import re
from dataclasses import asdict, dataclass

import anthropic

from ..config import Settings

log = logging.getLogger(__name__)

# Generators with a built-in pipeline template.
GENERATORS = {
    "tetris": "tetris_synthetic",
    "arithmetic": "arithmetic_synthetic",
}

_RULES: list[tuple[str, str, str | None, tuple[str, ...]]] = [
    ("game", "Game rules are fully known, so a simulator yields unlimited perfectly-labeled (state, action, reward) data.", None,
     ("tetris", "chess", "snake", "pong", "atari", "sudoku", "maze", "2048", "flappy", "minesweeper", "checkers",
      "connect four", "tic tac toe", "tic-tac-toe", "go game", "breakout", "pacman", "game")),
    ("physics", "Physical systems can be simulated with exact ground truth and controllable coverage.", None,
     ("physics", "pendulum", "projectile", "cartpole", "robot arm", "fluid", "collision", "orbit", "rigid body", "trajectory")),
    ("math", "Math problems can be generated with exact, verifiable answers at any scale.", "arithmetic",
     ("arithmetic", "math", "equation", "algebra", "addition", "multiplication", "calculator", "sum of")),
    ("tabular", "Synthetic tabular data avoids privacy issues and lets you control class balance.", None,
     ("synthetic tabular", "fraud simulation", "simulated transactions")),
]


@dataclass
class Advice:
    recommended: bool
    category: str | None
    reason: str
    generator: str | None
    template: str | None
    scaffold: str | None
    source: str = "rules"

    def to_json(self) -> dict:
        return asdict(self)


def scaffold_script(goal: str, category: str | None) -> str:
    """A starting point for a custom generator, using Gymnasium's env API."""
    return f'''"""Synthetic data generator scaffold for: {goal}

Fill in `MyEnv` with the rules of your domain, then run this script to log
(observation, action, reward) rows to Parquet.
"""
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq


class MyEnv:
    """Implement reset/step for your {category or "domain"} simulator."""

    def reset(self, seed=None):
        self.rng = np.random.default_rng(seed)
        self.state = np.zeros(4, dtype=np.float32)
        return self.state

    def step(self, action):
        # TODO: apply `action`, update self.state, compute reward and termination.
        reward, done = 0.0, self.rng.random() < 0.01
        return self.state, reward, done


def policy(state, rng):
    # TODO: replace with a heuristic or scripted expert; random is a baseline.
    return int(rng.integers(0, 2))


def main(episodes=1000, out="synthetic.parquet"):
    env, rows = MyEnv(), []
    rng = np.random.default_rng(0)
    for ep in range(episodes):
        state, done, t = env.reset(seed=ep), False, 0
        while not done and t < 1000:
            action = policy(state, rng)
            nxt, reward, done = env.step(action)
            rows.append({{"episode": ep, "t": t, "state": state.tolist(), "action": action,
                          "reward": reward, "next_state": nxt.tolist(), "done": done}})
            state, t = nxt, t + 1
    pq.write_table(pa.Table.from_pylist(rows), out)


if __name__ == "__main__":
    main()
'''


def advise_rules(goal: str) -> Advice:
    text = goal.lower()
    for category, reason, generator, keywords in _RULES:
        hits = [k for k in keywords if re.search(rf"\b{re.escape(k)}\b", text)]
        if not hits:
            continue
        gen = generator
        if category == "game" and "tetris" in text:
            gen = "tetris"
        return Advice(
            recommended=True,
            category=category,
            reason=reason,
            generator=gen,
            template=GENERATORS.get(gen) if gen else None,
            scaffold=None if gen else scaffold_script(goal, category),
        )
    return Advice(False, None, "Real-world data is likely needed; no simulator fits this goal.", None, None, None)


ADVICE_SCHEMA = {
    "type": "object",
    "properties": {
        "recommended": {"type": "boolean"},
        "category": {"type": "string", "enum": ["game", "physics", "math", "tabular", "other", "none"]},
        "reason": {"type": "string"},
        "generator": {"type": "string", "enum": [*GENERATORS, "none"]},
        "scaffold": {"type": "string"},
    },
    "required": ["recommended", "category", "reason", "generator", "scaffold"],
    "additionalProperties": False,
}

ADVISOR_PROMPT = f"""You advise ML teams on whether to simulate or generate training data instead of collecting it from the internet.

Recommend synthetic data when the domain has known rules or physics (games, simulations, math, procedural tasks) or when real data is scarce or sensitive. Built-in generators: {", ".join(GENERATORS)}. Pick one only if it matches the goal; otherwise use "none" and write a complete, runnable Python script in `scaffold` that simulates the domain and writes (state, action, reward) or (input, label) rows to a Parquet file with pyarrow. Leave `scaffold` empty when a built-in generator fits or synthetic data is not recommended. Keep `reason` to one or two sentences."""


async def advise_claude(goal: str, settings: Settings) -> Advice:
    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    response = await client.beta.messages.create(
        model=settings.planner_model,
        max_tokens=16000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        system=ADVISOR_PROMPT,
        messages=[{"role": "user", "content": f"Goal: {goal}"}],
        output_config={"effort": "medium", "format": {"type": "json_schema", "schema": ADVICE_SCHEMA}},
    )
    if response.stop_reason in ("refusal", "max_tokens"):
        raise RuntimeError(f"advisor stopped: {response.stop_reason}")
    data = json.loads(next(b.text for b in response.content if b.type == "text"))
    gen = data["generator"] if data["generator"] in GENERATORS else None
    return Advice(
        recommended=data["recommended"],
        category=None if data["category"] == "none" else data["category"],
        reason=data["reason"],
        generator=gen,
        template=GENERATORS.get(gen) if gen else None,
        scaffold=(data["scaffold"].strip() or None) if data["recommended"] and not gen else None,
        source="claude",
    )


async def advise(goal: str, settings: Settings) -> Advice:
    if settings.anthropic_api_key:
        try:
            return await advise_claude(goal, settings)
        except (anthropic.APIError, RuntimeError, ValueError, KeyError, StopIteration) as exc:
            log.warning("Claude advisor unavailable (%s); using rules", exc)
    return advise_rules(goal)
