"""Synthetic Data Advisor (plan §3.4): decide when simulation/generation beats collection."""
from __future__ import annotations

import re

from .generators import GENERATORS

SCAFFOLDS = {
    "game": '''"""Roll out a Gymnasium environment and log (obs, action, reward) to Parquet.
pip install gymnasium polars   (+ the env package, e.g. ale-py / tetris-gymnasium)"""
import gymnasium as gym, polars as pl
env = gym.make("{env_id}")
rows = []
for ep in range({episodes}):
    obs, _ = env.reset(seed=ep)
    done = False
    while not done:
        action = env.action_space.sample()        # swap in a heuristic / teacher policy
        nxt, reward, term, trunc, _ = env.step(action)
        rows.append({{"episode": ep, "obs": obs.tolist() if hasattr(obs, "tolist") else obs,
                      "action": int(action) if hasattr(action, "__int__") else action, "reward": float(reward)}})
        obs, done = nxt, term or trunc
pl.DataFrame(rows).write_parquet("rollouts.parquet")
''',
    "robotics": '''"""Collect simulated robot trajectories (PyBullet / MuJoCo via Gymnasium).
pip install "gymnasium[mujoco]" polars"""
import gymnasium as gym, polars as pl
env = gym.make("{env_id}")
rows = []
for ep in range({episodes}):
    obs, _ = env.reset(seed=ep)
    for t in range(1000):
        act = env.action_space.sample()
        nxt, r, term, trunc, _ = env.step(act)
        rows.append({{"episode": ep, "t": t, "obs": obs.tolist(), "action": act.tolist(), "reward": float(r)}})
        obs = nxt
        if term or trunc: break
pl.DataFrame(rows).write_parquet("trajectories.parquet")
''',
    "tabular": '''"""Fit a synthesizer to a small real table and sample a larger synthetic one.
pip install sdv polars"""
import polars as pl
from sdv.metadata import SingleTableMetadata
from sdv.single_table import GaussianCopulaSynthesizer
real = pl.read_csv("real.csv").to_pandas()
meta = SingleTableMetadata(); meta.detect_from_dataframe(real)
synth = GaussianCopulaSynthesizer(meta); synth.fit(real)
pl.from_pandas(synth.sample({episodes})).write_parquet("synthetic.parquet")
''',
    "vision": '''"""Procedurally render labeled images (shapes/text/digits) with Pillow — swap in Blender/BlenderProc for 3D.
pip install pillow polars"""
import random, pathlib
from PIL import Image, ImageDraw
out = pathlib.Path("synthetic_images"); labels = ["circle", "square", "triangle"]
for i in range({episodes}):
    lab = random.choice(labels); img = Image.new("RGB", (64, 64), "white"); d = ImageDraw.Draw(img)
    x, y, s = random.randint(5, 30), random.randint(5, 30), random.randint(15, 30)
    if lab == "circle": d.ellipse([x, y, x + s, y + s], fill="black")
    elif lab == "square": d.rectangle([x, y, x + s, y + s], fill="black")
    else: d.polygon([(x, y + s), (x + s, y + s), (x + s // 2, y)], fill="black")
    (out / lab).mkdir(parents=True, exist_ok=True); img.save(out / lab / f"{{i}}.png")
''',
}

_ENV_IDS = {"tetris": "tetris_gymnasium/Tetris", "pong": "ALE/Pong-v5", "breakout": "ALE/Breakout-v5", "cartpole": "CartPole-v1",
            "lunar": "LunarLander-v3", "car": "CarRacing-v3", "taxi": "Taxi-v3", "chess": "ALE/VideoChess-v5"}


def advise(goal: str, domain: str, subject: str) -> dict:
    g = goal.lower()
    gen = None
    if "tetris" in g:
        gen = "tetris"
    elif domain == "math" or re.search(r"arithmetic|add(ition)?|multipl", g):
        gen = "arithmetic"
    elif re.search(r"maze|grid ?world|path ?find|navigation", g):
        gen = "gridworld"

    recommended = gen is not None or domain in ("game", "robotics", "math")
    score = 0.9 if gen else {"game": 0.8, "robotics": 0.75, "math": 0.85, "tabular": 0.4, "vision": 0.35}.get(domain, 0.15)
    reasons = {
        "game": "Games have exact rules: a simulator gives unlimited, perfectly-labeled (state, action, reward) data for free.",
        "robotics": "Physics simulators produce unlimited safe trajectories; mix with real demos for sim-to-real.",
        "math": "Math problems can be generated and verified programmatically — no labeling cost.",
        "tabular": "If you have a small real table, a synthesizer can augment it (privacy-friendly), but collect real data first.",
        "vision": "Procedural/3D rendering can pre-train detectors, but real images are usually still needed.",
    }
    env_id = next((v for k, v in _ENV_IDS.items() if k in g), "CartPole-v1")
    scaffold = SCAFFOLDS.get(domain) or (SCAFFOLDS["game"] if gen else None)
    return {
        "recommended": recommended,
        "score": score,
        "generator": gen,
        "generator_info": {k: v for k, v in GENERATORS[gen].items() if k != "fn"} if gen else None,
        "rationale": reasons.get(domain, "Real-world data is likely the better source for this goal; synthetic data is optional."),
        "scaffold": scaffold.format(env_id=env_id, episodes=1000) if scaffold else None,
    }
