"""Render Tetris games to video frames, for testing the video pipeline and
for generating synthetic gameplay footage."""

from pathlib import Path

import cv2
import numpy as np

from .tetris import COLS, ROTATIONS, ROWS, fits, piece_id, play_game

CELL = 16
# BGR colors for piece ids 1..7.
COLORS = [(0, 0, 0), (240, 240, 0), (0, 240, 240), (240, 0, 160), (0, 240, 0), (0, 0, 240), (240, 0, 0), (0, 160, 240)]
FRAME_W, FRAME_H = 480, 400
BOARD_X, BOARD_Y = 160, 40


def draw(board: np.ndarray) -> np.ndarray:
    frame = np.full((FRAME_H, FRAME_W, 3), 40, dtype=np.uint8)
    cv2.putText(frame, "SCORE", (20, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (200, 200, 200), 1)
    x0, y0 = BOARD_X, BOARD_Y
    cv2.rectangle(frame, (x0 - 4, y0 - 4), (x0 + COLS * CELL + 3, y0 + ROWS * CELL + 3), (180, 180, 180), 4)
    frame[y0 : y0 + ROWS * CELL, x0 : x0 + COLS * CELL] = 0
    for r in range(ROWS):
        for c in range(COLS):
            v = int(board[r, c])
            if v:
                y, x = y0 + r * CELL, x0 + c * CELL
                cv2.rectangle(frame, (x + 1, y + 1), (x + CELL - 2, y + CELL - 2), COLORS[v], -1)
    return frame


def _overlay(board, shape, row, col, pid):
    out = board.copy()
    for r, c in shape:
        if row + r >= 0:
            out[row + r, col + c] = pid
    return out


def game_frames(agent="heuristic", seed=0, placements=10):
    """Yield (board_with_active_piece, label) per animation step.

    Each placement animates: spawn, rotate to target, shift to target column,
    fall row by row, lock (with line clears applied).
    """
    for step in play_game(agent, seed=seed, max_steps=placements):
        board, piece, pid = step.board, step.piece, piece_id(step.piece)
        rot, col, row = 0, 3, 0
        shape = ROTATIONS[piece][0]
        if not fits(board, shape, row, col):
            return
        yield _overlay(board, shape, row, col, pid), "spawn"
        while rot != step.rotation:
            nxt = ROTATIONS[piece][rot + 1]
            c = min(col, COLS - (max(cc for _, cc in nxt) + 1))
            if not fits(board, nxt, row, c):
                break
            rot, shape, col = rot + 1, nxt, c
            yield _overlay(board, shape, row, col, pid), "rotate"
        while col != step.column:
            d = 1 if step.column > col else -1
            if not fits(board, shape, row, col + d):
                break
            col += d
            yield _overlay(board, shape, row, col, pid), "right" if d > 0 else "left"
        while fits(board, shape, row + 1, col):
            row += 1
            yield _overlay(board, shape, row, col, pid), "down"
        locked = _overlay(board, shape, row, col, pid)
        full = np.all(locked > 0, axis=1)
        if full.any():
            locked = np.vstack([np.zeros((int(full.sum()), COLS), dtype=np.uint8), locked[~full]])
            yield locked, "clear"


def write_video(path: Path, agent="heuristic", seed=0, placements=10, fps=10) -> list[tuple[np.ndarray, str]]:
    """Render a game to `path` (mp4). Returns the ground-truth frame sequence."""
    frames = list(game_frames(agent, seed, placements))
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (FRAME_W, FRAME_H))
    # A title card first, like a menu screen before gameplay.
    title = np.full((FRAME_H, FRAME_W, 3), 90, dtype=np.uint8)
    cv2.putText(title, "PRESS START", (150, 200), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 2)
    for _ in range(fps):
        writer.write(title)
    for board, _ in frames:
        writer.write(draw(board))
    writer.release()
    return frames
