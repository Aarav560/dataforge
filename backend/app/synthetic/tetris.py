"""Minimal Tetris engine with heuristic/random agents.

Actions are placements: (rotation, column) followed by a hard drop, which is
the action space most Tetris RL work uses. Boards are 20x10 uint8 arrays where
0 is empty and 1-7 identify the piece that filled the cell.
"""

from dataclasses import dataclass

import numpy as np

ROWS, COLS = 20, 10
PIECES = "IOTSZJL"
_BASE = {
    "I": [(0, 0), (0, 1), (0, 2), (0, 3)],
    "O": [(0, 0), (0, 1), (1, 0), (1, 1)],
    "T": [(0, 0), (0, 1), (0, 2), (1, 1)],
    "S": [(0, 1), (0, 2), (1, 0), (1, 1)],
    "Z": [(0, 0), (0, 1), (1, 1), (1, 2)],
    "J": [(0, 0), (1, 0), (1, 1), (1, 2)],
    "L": [(0, 2), (1, 0), (1, 1), (1, 2)],
}
# NES scoring at level 0.
LINE_SCORES = {0: 0, 1: 40, 2: 100, 3: 300, 4: 1200}


def _normalize(cells):
    r0 = min(r for r, _ in cells)
    c0 = min(c for _, c in cells)
    return tuple(sorted((r - r0, c - c0) for r, c in cells))


def _rotations(cells):
    out, cur = [], _normalize(cells)
    for _ in range(4):
        if cur not in out:
            out.append(cur)
        cur = _normalize([(c, -r) for r, c in cur])
    return out


ROTATIONS: dict[str, list[tuple[tuple[int, int], ...]]] = {p: _rotations(c) for p, c in _BASE.items()}
# Shape -> (piece, rotation), used to identify pieces seen in video frames.
SHAPES = {shape: (p, i) for p, rots in ROTATIONS.items() for i, shape in enumerate(rots)}


def piece_id(piece: str) -> int:
    return PIECES.index(piece) + 1


def fits(board: np.ndarray, shape, row: int, col: int) -> bool:
    for r, c in shape:
        rr, cc = row + r, col + c
        if cc < 0 or cc >= COLS or rr >= ROWS:
            return False
        if rr >= 0 and board[rr, cc]:
            return False
    return True


def drop_row(board: np.ndarray, shape, col: int) -> int | None:
    """Lowest row the shape can rest at in `col`, or None if it can't enter."""
    if not fits(board, shape, 0, col):
        return None
    row = 0
    while fits(board, shape, row + 1, col):
        row += 1
    return row


def place(board: np.ndarray, piece: str, rotation: int, col: int) -> tuple[np.ndarray, int] | None:
    """Hard-drop and lock; returns (new_board, lines_cleared) or None if invalid."""
    shape = ROTATIONS[piece][rotation]
    row = drop_row(board, shape, col)
    if row is None:
        return None
    new = board.copy()
    for r, c in shape:
        new[row + r, col + c] = piece_id(piece)
    full = np.all(new > 0, axis=1)
    lines = int(full.sum())
    if lines:
        kept = new[~full]
        new = np.vstack([np.zeros((lines, COLS), dtype=np.uint8), kept])
    return new, lines


def placements(board: np.ndarray, piece: str):
    for rot, shape in enumerate(ROTATIONS[piece]):
        width = max(c for _, c in shape) + 1
        for col in range(COLS - width + 1):
            res = place(board, piece, rot, col)
            if res is not None:
                yield rot, col, res[0], res[1]


def features(board: np.ndarray) -> tuple[int, int, int]:
    """(aggregate height, holes, bumpiness)."""
    filled = board > 0
    any_filled = filled.any(axis=0)
    first = np.where(any_filled, filled.argmax(axis=0), ROWS)
    heights = ROWS - first
    below = np.cumsum(filled, axis=0) > 0
    holes = int((below & ~filled).sum())
    return int(heights.sum()), holes, int(np.abs(np.diff(heights)).sum())


# Weights from Yiyuan Lee's genetic-algorithm-tuned Tetris agent.
_W = (-0.510066, 0.760666, -0.35663, -0.184483)


def heuristic_score(board: np.ndarray, lines: int) -> float:
    h, holes, bump = features(board)
    return _W[0] * h + _W[1] * lines + _W[2] * holes + _W[3] * bump


class SevenBag:
    def __init__(self, rng: np.random.Generator):
        self.rng = rng
        self.bag: list[str] = []

    def next(self) -> str:
        if not self.bag:
            self.bag = list(self.rng.permutation(list(PIECES)))
        return self.bag.pop()


@dataclass
class Step:
    board: np.ndarray  # before the placement
    piece: str
    next_piece: str
    rotation: int
    column: int
    reward: int
    lines: int
    done: bool


def choose(board, piece, agent: str, rng: np.random.Generator, epsilon: float):
    options = list(placements(board, piece))
    if not options:
        return None
    if agent == "random" or rng.random() < epsilon:
        return options[rng.integers(len(options))]
    return max(options, key=lambda o: heuristic_score(o[2], o[3]))


def play_game(agent: str = "heuristic", seed: int = 0, max_steps: int = 500, epsilon: float = 0.0):
    """Yield one Step per placement until game over or max_steps."""
    rng = np.random.default_rng(seed)
    bag = SevenBag(rng)
    board = np.zeros((ROWS, COLS), dtype=np.uint8)
    piece, nxt = bag.next(), bag.next()
    for _ in range(max_steps):
        choice = choose(board, piece, agent, rng, epsilon)
        if choice is None:
            return
        rot, col, new_board, lines = choice
        # Game over when the next piece can't enter the board.
        done = not any(drop_row(new_board, s, 3) is not None for s in ROTATIONS[nxt][:1])
        yield Step(board, piece, nxt, rot, col, LINE_SCORES[lines], lines, done)
        if done:
            return
        board, piece, nxt = new_board, nxt, bag.next()
