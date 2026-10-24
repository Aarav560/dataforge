"""Tetris engine (bitboard) + heuristic/random agents → (board, piece, next_piece, action, reward) rows.

Board = list of H row bitmasks (row 0 = top, bit c = column c). The heuristic agent uses the
classic 4-feature evaluation (aggregate height, lines cleared, holes, bumpiness).
"""
from __future__ import annotations

import random

W, H = 10, 20
FULL = (1 << W) - 1

_BASE = {
    "I": [(0, 0), (0, 1), (0, 2), (0, 3)],
    "O": [(0, 0), (0, 1), (1, 0), (1, 1)],
    "T": [(0, 0), (0, 1), (0, 2), (1, 1)],
    "S": [(0, 1), (0, 2), (1, 0), (1, 1)],
    "Z": [(0, 0), (0, 1), (1, 1), (1, 2)],
    "J": [(0, 0), (1, 0), (1, 1), (1, 2)],
    "L": [(0, 2), (1, 0), (1, 1), (1, 2)],
}
PIECES = list(_BASE)
WEIGHTS = (-0.510066, 0.760666, -0.35663, -0.184483)


def _rotations(cells):
    out, cur = [], cells
    for _ in range(4):
        mr = min(r for r, _ in cur)
        mc = min(c for _, c in cur)
        norm = tuple(sorted((r - mr, c - mc) for r, c in cur))
        if norm not in out:
            out.append(norm)
        cur = [(c, -r) for r, c in cur]  # rotate 90° clockwise
    return out


ROTS = {p: _rotations(c) for p, c in _BASE.items()}
# Per piece/rotation: (row masks at col 0, height, width)
SHAPES = {
    p: [([sum(1 << c for r2, c in cells if r2 == r) for r in range(max(r for r, _ in cells) + 1)],
         max(r for r, _ in cells) + 1, max(c for _, c in cells) + 1) for cells in rots]
    for p, rots in ROTS.items()
}


def drop(board: list[int], masks: list[int], ph: int, col: int) -> tuple[list[int], int] | None:
    sm = [m << col for m in masks]
    y = -ph  # top row of piece
    while True:
        ny = y + 1
        if ny + ph > H or any(ny + i >= 0 and board[ny + i] & sm[i] for i in range(ph)):
            break
        y = ny
    if y < 0:
        return None  # topped out
    b = board[:]
    for i in range(ph):
        b[y + i] |= sm[i]
    kept = [r for r in b if r != FULL]
    lines = H - len(kept)
    if lines:
        b = [0] * lines + kept
    return b, lines


def evaluate(board: list[int], lines: int) -> tuple[float, int, int]:
    seen = 0
    holes = 0
    heights = [0] * W
    for r, row in enumerate(board):
        new = row & ~seen
        if new:
            h = H - r
            for c in range(W):
                if new >> c & 1:
                    heights[c] = h
        holes += bin(seen & ~row).count("1")
        seen |= row
    agg = sum(heights)
    bump = sum(abs(heights[i] - heights[i + 1]) for i in range(W - 1))
    score = WEIGHTS[0] * agg + WEIGHTS[1] * lines + WEIGHTS[2] * holes + WEIGHTS[3] * bump
    return score, agg, holes


def legal_moves(board: list[int], piece: str):
    for rot, (masks, ph, pw) in enumerate(SHAPES[piece]):
        for col in range(W - pw + 1):
            res = drop(board, masks, ph, col)
            if res is not None:
                yield rot, col, res[0], res[1]


def encode(board: list[int]) -> str:
    return "".join(format(row, f"0{W}b")[::-1] for row in board)  # row-major, col 0 first


def play_games(n_games: int, agent: str = "heuristic", epsilon: float = 0.05, max_moves: int = 500, seed: int = 0,
               on_progress=None) -> dict[str, list]:
    rng = random.Random(seed)
    cols: dict[str, list] = {k: [] for k in ("game", "move", "board", "piece", "next_piece", "rotation", "column",
                                             "lines_cleared", "reward", "done", "agent", "height", "holes")}
    label = agent if agent == "random" else f"heuristic(eps={epsilon})"
    for g in range(n_games):
        board = [0] * H
        bag = PIECES[:]
        rng.shuffle(bag)
        piece = bag.pop()
        start = len(cols["game"])
        for m in range(max_moves):
            if not bag:
                bag = PIECES[:]
                rng.shuffle(bag)
            nxt = bag[-1]
            moves = list(legal_moves(board, piece))
            if not moves:
                break
            if agent == "random" or rng.random() < epsilon:
                rot, col, nb, lines = moves[rng.randrange(len(moves))]
                _, agg, holes = evaluate(nb, lines)
            else:
                best = None
                for mv in moves:
                    ev = evaluate(mv[2], mv[3])
                    if best is None or ev[0] > best[1][0]:
                        best = (mv, ev)
                (rot, col, nb, lines), (_, agg, holes) = best
            cols["game"].append(g); cols["move"].append(m); cols["board"].append(encode(board))
            cols["piece"].append(piece); cols["next_piece"].append(nxt); cols["rotation"].append(rot)
            cols["column"].append(col); cols["lines_cleared"].append(lines)
            cols["reward"].append(float((0, 1, 3, 5, 8)[lines]) + 0.01)
            cols["done"].append(False); cols["agent"].append(label)
            cols["height"].append(agg); cols["holes"].append(holes)
            board = nb
            piece = bag.pop()
        if len(cols["game"]) > start:
            cols["done"][-1] = True
            if len(cols["game"]) - start < max_moves:  # topped out (not truncated)
                cols["reward"][-1] -= 10.0
        if on_progress and (g % max(1, n_games // 50) == 0):
            on_progress((g + 1) / n_games, f"game {g + 1}/{n_games} · {len(cols['game']):,} rows")
    return cols
