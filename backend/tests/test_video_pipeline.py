"""Ground-truth check: render engine games → run Video → State/Action pipeline → compare inferred placements."""
import asyncio
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("DATAFORGE_HOME", tempfile.mkdtemp(prefix="dataforge_test_"))  # never touch real projects

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dataforge import db  # noqa: E402
from dataforge.config import RAW_DIR  # noqa: E402
from dataforge.pipelines import TEMPLATES, run_pipeline  # noqa: E402
from render_tetris_video import main as render  # noqa: E402


class Job:
    def progress(self, *a): pass
    def log(self, m, **k): print(" ", m)


async def run() -> tuple[int, float]:
    pid = db.insert("projects", goal="video pipeline ground-truth test", plan_json={})["id"]
    d = RAW_DIR / pid / "rendered"
    cols = render(d / "tetris.mp4", n_moves=120, fps=10, frames_per_move=3)
    db.insert("assets", project_id=pid, result_id=None, storage_path=str(d), status="done", size_bytes=0)
    graph = TEMPLATES["video_state_action"]["graph"]
    for n in graph["nodes"]:
        if n["data"]["op"] == "extract_frames":
            n["data"]["params"]["every_sec"] = 0.1
        if n["data"]["op"] == "dedupe":
            n["data"]["params"]["subset"] = ["frame_idx"]
    ctx = await run_pipeline(Job(), pid, graph)
    t = ctx["table"].sort("frame_idx")
    truth = [c for c, ln in zip(cols["column"], cols["lines_cleared"]) if ln == 0]
    inferred = [c for c, ln in zip(t["column"].to_list(), t["lines_cleared"].to_list()) if ln == 0 and c is not None]
    hits = sum(1 for a, b in zip(truth, inferred) if a == b)
    acc = hits / max(1, min(len(truth), len(inferred)))
    print(f"moves={len(cols['column'])} transitions={t.height} column-accuracy(non-clearing)={acc:.2%}")
    return t.height, acc


def test_video_pipeline_recovers_placements():
    n, acc = asyncio.run(run())
    assert n >= 100
    assert acc >= 0.9


if __name__ == "__main__":
    asyncio.run(run())
