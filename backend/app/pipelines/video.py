"""Gameplay video -> board states -> (state, piece, action) rows.

Steps: sample frames, drop non-gameplay scenes (menus, transitions), locate the
playfield, grid-sample cells into a binary matrix, then diff consecutive
states to infer the action taken.
"""

from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from ..synthetic.tetris import SHAPES

VIDEO_EXTS = {".mp4", ".mkv", ".webm", ".mov", ".avi"}


@dataclass
class VideoRef:
    path: Path
    fps: float
    frame_count: int
    # Sampled frame indices kept for processing.
    frames: list[int] = field(default_factory=list)
    # Playfield bbox per kept frame index: (x, y, w, h).
    boxes: dict[int, tuple[int, int, int, int]] = field(default_factory=dict)


def probe(path: Path) -> VideoRef:
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise ValueError(f"cannot open video {path.name}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    cap.release()
    return VideoRef(path, fps, count)


def sample_frames(ref: VideoRef, sample_fps: float | None) -> VideoRef:
    step = 1 if not sample_fps or sample_fps >= ref.fps else max(1, round(ref.fps / sample_fps))
    ref.frames = list(range(0, ref.frame_count, step))
    return ref


def iter_frames(ref: VideoRef, wanted: list[int]):
    """Yield (index, frame) for the wanted indices, reading sequentially."""
    want = set(wanted)
    if not want:
        return
    last = max(want)
    cap = cv2.VideoCapture(str(ref.path))
    idx = 0
    try:
        while idx <= last:
            ok = cap.grab()
            if not ok:
                break
            if idx in want:
                ok, frame = cap.retrieve()
                if ok:
                    yield idx, frame
            idx += 1
    finally:
        cap.release()


def scene_ranges(ref: VideoRef, threshold: float = 27.0) -> list[tuple[int, int]]:
    """Scene boundaries as [start, end) frame ranges via PySceneDetect."""
    from scenedetect import ContentDetector, detect

    scenes = detect(str(ref.path), ContentDetector(threshold=threshold, min_scene_len=3))
    if not scenes:
        return [(0, ref.frame_count)]
    return [(s.frame_num, e.frame_num) for s, e in scenes]


def detect_board(frame: np.ndarray, aspect: float = 0.5, tolerance: float = 0.08, min_area: float = 0.02):
    """Find the playfield interior: the tightest rectangle with the expected aspect."""
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 50, 150)
    contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    frame_area = frame.shape[0] * frame.shape[1]
    candidates = []
    for cnt in contours:
        x, y, w, h = cv2.boundingRect(cnt)
        if h == 0 or w * h < min_area * frame_area:
            continue
        if abs(w / h - aspect) > tolerance:
            continue
        # Require a rectangle, not an arbitrary blob with a matching bbox.
        if cv2.contourArea(cnt) < 0.85 * w * h and len(cv2.approxPolyDP(cnt, 0.02 * cv2.arcLength(cnt, True), True)) != 4:
            continue
        candidates.append((x, y, w, h))
    if not candidates:
        return None
    biggest = max(w * h for _, _, w, h in candidates)
    # Border lines produce outer and inner edges; the inner one is the playfield.
    close = [c for c in candidates if c[2] * c[3] >= 0.8 * biggest]
    x, y, w, h = min(close, key=lambda c: c[2] * c[3])
    pad = max(1, round(min(w, h) * 0.005))
    return x + pad, y + pad, w - 2 * pad, h - 2 * pad


def grid_sample(frame: np.ndarray, box, rows: int = 20, cols: int = 10, threshold: int = 50) -> np.ndarray:
    """Binary occupancy matrix from the central patch of each cell."""
    x, y, w, h = box
    region = frame[y : y + h, x : x + w]
    bright = region.max(axis=2) if region.ndim == 3 else region
    ch, cw = h / rows, w / cols
    grid = np.zeros((rows, cols), dtype=np.uint8)
    for r in range(rows):
        for c in range(cols):
            y0, y1 = int((r + 0.25) * ch), int((r + 0.75) * ch)
            x0, x1 = int((c + 0.25) * cw), int((c + 0.75) * cw)
            grid[r, c] = bright[y0 : max(y1, y0 + 1), x0 : max(x1, x0 + 1)].mean() > threshold
    return grid


def identify(cells: np.ndarray) -> tuple[str, int] | None:
    """Map a set of 4 (row, col) cells to (piece, rotation)."""
    if len(cells) != 4:
        return None
    r0, c0 = cells[:, 0].min(), cells[:, 1].min()
    shape = tuple(sorted((int(r - r0), int(c - c0)) for r, c in cells))
    return SHAPES.get(shape)


def _shape(cells: np.ndarray):
    found = identify(cells)
    return found if found else (None, None)


def _initial_locked(state: np.ndarray, spawn_rows: int):
    """If the first state already shows a piece near the top, split it off."""
    n, labels = cv2.connectedComponents(state.astype(np.uint8), connectivity=4)
    for i in range(1, n):
        cells = np.argwhere(labels == i)
        if len(cells) == 4 and cells[:, 0].max() < spawn_rows:
            locked = state.astype(bool).copy()
            locked[labels == i] = False
            return locked, _shape(cells)[0]
    return None, None


def board_for_scene(ref: VideoRef, frames: list[int], probes: int = 9):
    """Detect the playfield on a few frames of a scene and keep the tightest box.

    Pieces touching the border can merge with its inner edge on some frames,
    so a single frame may only yield the outer edge.
    """
    if not frames:
        return None
    pick = frames[:: max(1, len(frames) // probes)][:probes]
    found = [b for _, f in iter_frames(ref, pick) if (b := detect_board(f)) is not None]
    if not found:
        return None
    return min(found, key=lambda b: b[2] * b[3])


def infer_actions(states: list[np.ndarray], spawn_rows: int = 4) -> list[tuple[str, str | None]]:
    """Label each transition states[i] -> states[i+1].

    Tracks the locked board so the active piece is always `state & ~locked`:
    a spawn is 4 new cells near the top with nothing removed; after that,
    changes of the active piece are moves/rotations; rows disappearing is a
    line clear. A lock itself is invisible and shows up as the next spawn.
    """
    labels: list[tuple[str, str | None]] = []
    locked, piece = _initial_locked(states[0], spawn_rows) if states else (None, None)
    for before, after in zip(states, states[1:]):
        b, a = before.astype(bool), after.astype(bool)
        added, removed = a & ~b, b & ~a
        n_add, n_rem = int(added.sum()), int(removed.sum())

        if n_rem == 0 and n_add == 4 and np.argwhere(added)[:, 0].max() < spawn_rows:
            locked = b
            piece = _shape(np.argwhere(added))[0]
            labels.append(("spawn", piece))
            continue
        if n_rem >= 10 and n_rem > n_add:
            locked = a
            labels.append(("clear", piece))
            continue
        if locked is None:
            labels.append(("unknown", None))
            continue
        prev_cells, next_cells = np.argwhere(b & ~locked), np.argwhere(a & ~locked)
        if len(prev_cells) != 4 or len(next_cells) != 4:
            labels.append(("unknown", piece))
            continue
        (p1, r1), (p2, r2) = _shape(prev_cells), _shape(next_cells)
        piece = p2 or p1 or piece
        if r1 is not None and r2 is not None and r1 != r2:
            labels.append(("rotate", piece))
            continue
        dr = next_cells[:, 0].mean() - prev_cells[:, 0].mean()
        dc = next_cells[:, 1].mean() - prev_cells[:, 1].mean()
        if abs(dr) > abs(dc):
            labels.append(("down" if dr > 0 else "unknown", piece))
        else:
            labels.append(("right" if dc > 0 else "left", piece))
    return labels
