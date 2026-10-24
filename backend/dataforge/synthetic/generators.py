"""Built-in synthetic data generators. Each returns a dict of equal-length columns."""
from __future__ import annotations

from collections import deque

import numpy as np

from . import tetris


def gen_tetris(rows_target: int = 20000, agent: str = "heuristic", epsilon: float = 0.05, seed: int = 0, on_progress=None):
    # ~150-500 moves per heuristic game (capped), so derive game count from target rows.
    per_game = 40 if agent == "random" else 300
    n_games = max(1, rows_target // per_game)
    return tetris.play_games(n_games, agent=agent, epsilon=epsilon, max_moves=min(500, max(50, rows_target)), seed=seed,
                             on_progress=on_progress)


def gen_arithmetic(rows_target: int = 20000, max_digits: int = 4, seed: int = 0, on_progress=None):
    rng = np.random.default_rng(seed)
    ops = ["+", "-", "*", "//"]
    out = {"question": [], "answer": [], "op": [], "difficulty": []}
    for i in range(rows_target):
        d = int(rng.integers(1, max_digits + 1))
        a, b = int(rng.integers(0, 10 ** d)), int(rng.integers(1, 10 ** d))
        op = ops[int(rng.integers(len(ops)))]
        ans = {"+": a + b, "-": a - b, "*": a * b, "//": a // b}[op]
        out["question"].append(f"What is {a} {op} {b}?")
        out["answer"].append(str(ans)); out["op"].append(op); out["difficulty"].append(d)
        if on_progress and i % max(1, rows_target // 20) == 0:
            on_progress(i / rows_target, f"{i:,} problems")
    return out


def gen_gridworld(rows_target: int = 20000, size: int = 8, wall_p: float = 0.2, seed: int = 0, on_progress=None):
    """Random mazes + BFS-optimal actions → (state, action) imitation-learning pairs."""
    rng = np.random.default_rng(seed)
    moves = {"up": (-1, 0), "down": (1, 0), "left": (0, -1), "right": (0, 1)}
    out = {"episode": [], "grid": [], "agent": [], "goal": [], "action": [], "steps_to_goal": []}
    ep = 0
    while len(out["action"]) < rows_target:
        grid = (rng.random((size, size)) < wall_p).astype(np.uint8)
        free = list(zip(*np.where(grid == 0)))
        if len(free) < 2:
            continue
        s, g = (free[i] for i in rng.choice(len(free), 2, replace=False))
        # BFS from goal → distance map
        dist = {g: 0}
        q = deque([g])
        while q:
            cur = q.popleft()
            for dr, dc in moves.values():
                n = (cur[0] + dr, cur[1] + dc)
                if 0 <= n[0] < size and 0 <= n[1] < size and not grid[n] and n not in dist:
                    dist[n] = dist[cur] + 1
                    q.append(n)
        if s not in dist:
            continue
        pos = s
        enc = "".join(map(str, grid.flat))
        while pos != g:
            name, (dr, dc) = min(((k, v) for k, v in moves.items() if (pos[0] + v[0], pos[1] + v[1]) in dist),
                                 key=lambda kv: dist[(pos[0] + kv[1][0], pos[1] + kv[1][1])])
            out["episode"].append(ep); out["grid"].append(enc); out["agent"].append(f"{pos[0]},{pos[1]}")
            out["goal"].append(f"{g[0]},{g[1]}"); out["action"].append(name); out["steps_to_goal"].append(dist[pos])
            pos = (pos[0] + dr, pos[1] + dc)
        ep += 1
        if on_progress and ep % 50 == 0:
            on_progress(min(1.0, len(out["action"]) / rows_target), f"{ep} mazes · {len(out['action']):,} rows")
    return out


GENERATORS = {
    "tetris": {"fn": gen_tetris, "label": "Tetris engine self-play",
               "schema": "game, move, board(200-bit str), piece, next_piece, rotation, column, lines_cleared, reward, done",
               "params": {"rows_target": 20000, "agent": "heuristic", "epsilon": 0.05}},
    "arithmetic": {"fn": gen_arithmetic, "label": "Arithmetic Q/A pairs",
                   "schema": "question, answer, op, difficulty", "params": {"rows_target": 20000, "max_digits": 4}},
    "gridworld": {"fn": gen_gridworld, "label": "Grid-maze optimal-action demos",
                  "schema": "episode, grid, agent, goal, action, steps_to_goal", "params": {"rows_target": 20000, "size": 8}},
}
