"""GitHub repos/code + RL environment catalog (Gymnasium, PettingZoo, community envs)."""
from __future__ import annotations

import re
from urllib.parse import urlencode

from .. import http
from ..config import GITHUB_TOKEN
from .base import Connector, Hit


def _gh_headers() -> dict:
    h = {"Accept": "application/vnd.github+json"}
    if GITHUB_TOKEN:
        h["Authorization"] = f"Bearer {GITHUB_TOKEN}"
    return h


async def _gh_repo_search(q: str, limit: int) -> list[dict]:
    r = await http.get(f"https://api.github.com/search/repositories?{urlencode({'q': q, 'sort': 'stars', 'per_page': limit})}",
                       headers=_gh_headers())
    if r.status_code == 403:
        raise RuntimeError("GitHub rate limit hit (set a free GITHUB_TOKEN to raise it)")
    r.raise_for_status()
    return r.json().get("items", [])


def _repo_hit(source: str, kind: str, d: dict) -> Hit:
    return Hit(
        source=source, kind=kind, url=d["html_url"], title=d["full_name"], description=d.get("description") or "",
        size_bytes=(d.get("size") or 0) * 1024, format=d.get("language"),
        license=(d.get("license") or {}).get("spdx_id"),
        meta={"stars": d.get("stargazers_count", 0), "forks": d.get("forks_count", 0), "updated": d.get("pushed_at"),
              "topics": d.get("topics", []), "archived": d.get("archived", False)},
        download={"type": "github", "repo": d["full_name"], "branch": d.get("default_branch", "main")},
    )


class GitHubRepos(Connector):
    """Repos with checkpoints / datasets / pretrained agents."""
    name, label, kinds = "github", "GitHub", ("model", "dataset", "code")
    max_queries = 1

    async def search(self, query, limit):
        return [_repo_hit(self.name, "model" if re.search(r"agent|model|pretrain|checkpoint", query, re.I) else "code", d)
                for d in await _gh_repo_search(query, limit)]


class GitHubCode(Connector):
    """Scrapers, loaders and preprocessing scripts. Uses code search when a free token is set."""
    name, label, kinds = "github_code", "GitHub Code", ("code",)
    max_queries = 1

    async def search(self, query, limit):
        if GITHUB_TOKEN:
            r = await http.get(f"https://api.github.com/search/code?{urlencode({'q': query, 'per_page': limit})}", headers=_gh_headers())
            if r.status_code == 200:
                return [Hit(source=self.name, kind="code", url=i["html_url"], title=f"{i['repository']['full_name']}/{i['path']}",
                            description=i["repository"].get("description") or "", format=i["name"].rsplit(".", 1)[-1],
                            meta={"repo": i["repository"]["full_name"]},
                            download={"type": "url", "url": i["html_url"].replace("github.com", "raw.githubusercontent.com").replace("/blob/", "/")})
                        for i in r.json().get("items", [])]
        return [_repo_hit(self.name, "code", d) for d in await _gh_repo_search(f"{query} in:name,description,readme", limit)]


# Curated catalog of free simulators. Matched locally (zero network).
ENV_CATALOG = [
    ("Gymnasium · Classic Control", "CartPole, MountainCar, Acrobot, Pendulum", "gymnasium", "https://gymnasium.farama.org/environments/classic_control/", "MIT", ["cartpole", "pendulum", "control", "balance"]),
    ("Gymnasium · Box2D", "LunarLander, BipedalWalker, CarRacing", "gymnasium[box2d]", "https://gymnasium.farama.org/environments/box2d/", "MIT", ["car", "racing", "driving", "lander", "walker", "physics"]),
    ("Gymnasium · MuJoCo", "Ant, HalfCheetah, Humanoid, Hopper — continuous control", "gymnasium[mujoco]", "https://gymnasium.farama.org/environments/mujoco/", "MIT", ["robot", "locomotion", "humanoid", "physics", "arm"]),
    ("Gymnasium · Toy Text", "FrozenLake, Taxi, Blackjack, CliffWalking", "gymnasium", "https://gymnasium.farama.org/environments/toy_text/", "MIT", ["grid", "blackjack", "taxi", "maze", "card"]),
    ("ALE · Atari 2600", "100+ Atari games incl. Tetris, Pong, Breakout, Space Invaders", "ale-py", "https://ale.farama.org/environments/", "GPL-2.0", ["atari", "game", "tetris", "pong", "breakout", "arcade", "pacman", "retro"]),
    ("PettingZoo", "Multi-agent: chess, go, connect four, poker, MPE, Atari multi-player", "pettingzoo", "https://pettingzoo.farama.org/", "MIT", ["chess", "go", "poker", "multi", "board", "card", "connect"]),
    ("MiniGrid / BabyAI", "Grid-world navigation & language-conditioned tasks", "minigrid", "https://minigrid.farama.org/", "Apache-2.0", ["grid", "maze", "navigation", "language"]),
    ("Procgen", "16 procedurally generated games for generalization", "procgen", "https://github.com/openai/procgen", "MIT", ["game", "platformer", "generalization", "arcade"]),
    ("MetaWorld", "50 robot manipulation tasks", "metaworld", "https://github.com/Farama-Foundation/Metaworld", "MIT", ["robot", "manipulation", "arm", "grasp"]),
    ("Highway-env", "Autonomous driving decision-making", "highway-env", "https://github.com/Farama-Foundation/HighwayEnv", "MIT", ["driving", "car", "traffic", "autonomous", "highway"]),
    ("NLE (NetHack)", "NetHack Learning Environment", "nle", "https://github.com/heiner/nle", "NetHack GPL", ["nethack", "roguelike", "game", "dungeon"]),
    ("MineRL", "Minecraft environments + human demonstrations", "minerl", "https://minerl.io/", "MIT", ["minecraft", "craft", "game", "demonstration"]),
    ("Craftax", "JAX Crafter/Minecraft-like open-ended env", "craftax", "https://github.com/MichaelTMatthews/Craftax", "MIT", ["minecraft", "craft", "survival", "jax"]),
    ("gym-tetris", "NES Tetris via nes-py emulator", "gym-tetris", "https://github.com/Kautenja/gym-tetris", "MIT", ["tetris", "nes", "puzzle", "game"]),
    ("tetris-gymnasium", "Modern Gymnasium Tetris env (Farama-compatible)", "tetris-gymnasium", "https://github.com/Max-We/Tetris-Gymnasium", "MIT", ["tetris", "puzzle", "game", "block"]),
    ("gym-super-mario-bros", "NES Super Mario Bros", "gym-super-mario-bros", "https://github.com/Kautenja/gym-super-mario-bros", "MIT", ["mario", "platformer", "nes", "game"]),
    ("OpenSpiel", "70+ games: board, card, poker, auctions", "open_spiel", "https://github.com/google-deepmind/open_spiel", "Apache-2.0", ["chess", "poker", "go", "card", "board", "game theory"]),
    ("python-chess", "Chess move generation + engine interface", "chess", "https://github.com/niklasf/python-chess", "GPL-3.0", ["chess"]),
    ("snake-gym", "Snake game environments", "gym-snake", "https://github.com/grantsrb/Gym-Snake", "MIT", ["snake", "game"]),
    ("Flappy Bird Gymnasium", "Flappy Bird env", "flappy-bird-gymnasium", "https://github.com/markub3327/flappy-bird-gymnasium", "MIT", ["flappy", "bird", "game"]),
    ("2048 gym", "2048 puzzle env", "gym-2048", "https://github.com/rgal/gym-2048", "MIT", ["2048", "puzzle", "game"]),
    ("CARLA", "Open-source urban driving simulator", "carla", "https://carla.org/", "MIT", ["driving", "car", "autonomous", "self-driving", "lidar"]),
    ("PyBullet Gym", "Bullet physics robotics envs", "pybullet", "https://github.com/bulletphysics/bullet3", "Zlib", ["robot", "physics", "drone", "arm"]),
    ("Stockfish (via python-chess)", "Strong chess engine — generate labeled positions", "stockfish", "https://stockfishchess.org/", "GPL-3.0", ["chess", "engine"]),
]


class Environments(Connector):
    name, label, kinds = "environments", "RL Environments", ("environment",)
    max_queries = 1

    async def search(self, query, limit):
        terms = set(re.findall(r"[a-z0-9]+", query.lower()))
        scored = []
        for title, desc, pkg, url, lic, kws in ENV_CATALOG:
            s = sum(2 for k in kws if k in terms or any(k in t for t in terms if len(t) > 3)) + sum(1 for t in terms if len(t) > 3 and t in desc.lower())
            if s:
                scored.append((s, Hit(source=self.name, kind="environment", url=url, title=title, description=desc,
                                      license=lic, format="python",
                                      meta={"pip": pkg, "stars": None, "install": f"pip install {pkg}"},
                                      download={"type": "pip", "package": pkg})))
        scored.sort(key=lambda x: -x[0])
        hits = [h for _, h in scored[:limit]]
        # Community envs from GitHub (topic search).
        try:
            for d in await _gh_repo_search(f"{query} gym environment", max(3, limit // 2)):
                hits.append(_repo_hit(self.name, "environment", d))
        except Exception:  # noqa: BLE001 — catalog results still useful
            pass
        return hits[:limit + 6]
