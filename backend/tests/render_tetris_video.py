"""Render engine games to an MP4 (NES-like look) — ground truth for testing the video → state/action pipeline."""
from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dataforge.synthetic import tetris  # noqa: E402

COLORS = {"I": (230, 200, 40), "O": (40, 210, 230), "T": (200, 60, 200), "S": (60, 200, 60), "Z": (60, 60, 220),
          "J": (220, 120, 40), "L": (40, 140, 240)}


def render(board_str: str, cell=16, ox=120, oy=20, W=480, H=360) -> np.ndarray:
    img = np.full((H, W, 3), (35, 30, 30), np.uint8)
    cv2.rectangle(img, (ox - 4, oy - 4), (ox + 10 * cell + 3, oy + 20 * cell + 3), (200, 200, 200), 2)
    cv2.rectangle(img, (ox, oy), (ox + 10 * cell - 1, oy + 20 * cell - 1), (0, 0, 0), -1)
    for i, ch in enumerate(board_str):
        if ch == "1":
            r, c = divmod(i, 10)
            cv2.rectangle(img, (ox + c * cell + 1, oy + r * cell + 1), (ox + (c + 1) * cell - 2, oy + (r + 1) * cell - 2), (60, 170, 230), -1)
    cv2.putText(img, "SCORE", (330, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (230, 230, 230), 1)
    return img


def main(out: Path, n_moves=120, fps=10, frames_per_move=3):
    cols = tetris.play_games(1, max_moves=n_moves, seed=7)
    out.parent.mkdir(parents=True, exist_ok=True)
    vw = cv2.VideoWriter(str(out), cv2.VideoWriter_fourcc(*"mp4v"), fps, (480, 360))
    boards = cols["board"]
    for b in boards:
        for _ in range(frames_per_move):
            vw.write(render(b))
    vw.release()
    return cols


if __name__ == "__main__":
    out = Path(sys.argv[1])
    out.parent.mkdir(parents=True, exist_ok=True)
    c = main(out)
    print(f"wrote {out} · {len(c['board'])} moves · lines {sum(c['lines_cleared'])}")
