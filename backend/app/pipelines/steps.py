"""Built-in pipeline steps."""

import itertools

import numpy as np
import pyarrow as pa

from ..synthetic import tetris
from . import video as V
from .engine import Context, Env, Param, PipelineError, step


@step(
    "assets",
    "Downloaded assets",
    "source",
    "Start from files gathered by downloads.",
    {"asset_ids": Param("int_list", required=True, help="Asset ids to read")},
    source=True,
)
def assets_step(ctx: Context, p: dict, env: Env) -> Context:
    out = Context()
    for aid in p["asset_ids"]:
        root = env.asset_paths.get(aid)
        if root is None or not root.exists():
            raise PipelineError(f"asset {aid} is not downloaded")
        for f in sorted(x for x in root.rglob("*") if x.is_file()):
            out.files.append(f)
            if f.suffix.lower() in V.VIDEO_EXTS:
                out.videos.append(V.probe(f))
    return out


@step(
    "tetris_synthetic",
    "Synthetic Tetris games",
    "source",
    "Play games with a heuristic or random agent and log (state, action, reward).",
    {
        "games": Param("int", 20, "Number of games"),
        "agent": Param("choice", "heuristic", "Policy", ["heuristic", "random", "mixed"]),
        "epsilon": Param("float", 0.1, "Random-move probability for the mixed agent"),
        "max_steps": Param("int", 300, "Placement cap per game"),
        "seed": Param("int", 0, "Base RNG seed"),
    },
    source=True,
)
def tetris_step(ctx: Context, p: dict, env: Env) -> Context:
    if p["games"] < 1 or p["games"] > 100_000:
        raise PipelineError("games must be between 1 and 100000")
    agent = "heuristic" if p["agent"] == "mixed" else p["agent"]
    eps = p["epsilon"] if p["agent"] == "mixed" else 0.0
    cols: dict[str, list] = {k: [] for k in ("game", "step", "state", "piece", "next_piece", "rotation", "column", "reward", "lines", "done")}
    for g in range(p["games"]):
        for i, s in enumerate(tetris.play_game(agent, seed=p["seed"] + g, max_steps=p["max_steps"], epsilon=eps)):
            cols["game"].append(g)
            cols["step"].append(i)
            cols["state"].append((s.board > 0).astype(np.uint8).ravel().tolist())
            cols["piece"].append(s.piece)
            cols["next_piece"].append(s.next_piece)
            cols["rotation"].append(s.rotation)
            cols["column"].append(s.column)
            cols["reward"].append(s.reward)
            cols["lines"].append(s.lines)
            cols["done"].append(s.done)
        if (g + 1) % max(1, p["games"] // 10) == 0:
            env.log(f"{g + 1}/{p['games']} games, {len(cols['game'])} rows")
    schema = pa.schema(
        [
            ("game", pa.int32()), ("step", pa.int32()), ("state", pa.list_(pa.uint8())),
            ("piece", pa.string()), ("next_piece", pa.string()), ("rotation", pa.int8()),
            ("column", pa.int8()), ("reward", pa.int32()), ("lines", pa.int8()), ("done", pa.bool_()),
        ]
    )
    return Context(table=pa.table(cols, schema=schema))


def _need_videos(ctx: Context, label: str) -> None:
    if not ctx.videos:
        raise PipelineError(f"{label} needs video input")


@step("sample_frames", "Sample frames", "video", "Pick frames at a fixed rate.", {"fps": Param("float", 0.0, "Frames per second (0 = every frame)")})
def sample_step(ctx: Context, p: dict, env: Env) -> Context:
    _need_videos(ctx, "Sample frames")
    for ref in ctx.videos:
        V.sample_frames(ref, p["fps"] or None)
    return ctx


@step(
    "scene_filter",
    "Scene filter",
    "video",
    "Split into scenes (PySceneDetect) and drop those without a playfield: menus, transitions, cutaways.",
    {"threshold": Param("float", 27.0, "Content-change threshold"), "min_frames": Param("int", 5, "Drop shorter scenes")},
)
def scene_step(ctx: Context, p: dict, env: Env) -> Context:
    _need_videos(ctx, "Scene filter")
    for ref in ctx.videos:
        frames = ref.frames or list(range(ref.frame_count))
        kept: list[int] = []
        scenes = V.scene_ranges(ref, p["threshold"])
        for a, b in scenes:
            in_scene = [i for i in frames if a <= i < b]
            if len(in_scene) < p["min_frames"]:
                continue
            box = V.board_for_scene(ref, in_scene)
            if box is None:
                continue
            kept.extend(in_scene)
            ref.boxes.update({i: box for i in in_scene})
        env.log(f"{ref.path.name}: kept {len(kept)}/{len(frames)} frames from {len(scenes)} scenes")
        ref.frames = kept
    return ctx


def _parse_box(text: str) -> tuple[int, int, int, int] | None:
    if not text:
        return None
    try:
        x, y, w, h = (int(v) for v in text.split(","))
    except ValueError as exc:
        raise PipelineError("box must be 'x,y,w,h'") from exc
    return x, y, w, h


@step(
    "detect_board",
    "Detect board",
    "video",
    "Locate the playfield. Uses the per-scene detection from the scene filter, or a manual box.",
    {
        "box": Param("str", "", "Manual playfield box 'x,y,w,h' (overrides detection)"),
        "aspect": Param("float", 0.5, "Playfield width / height"),
    },
)
def board_step(ctx: Context, p: dict, env: Env) -> Context:
    _need_videos(ctx, "Detect board")
    manual = _parse_box(p["box"])
    for ref in ctx.videos:
        frames = ref.frames or list(range(ref.frame_count))
        if manual:
            ref.boxes = {i: manual for i in frames}
            continue
        missing = [i for i in frames if i not in ref.boxes]
        if missing:
            box = V.board_for_scene(ref, missing)
            if box is None and not ref.boxes:
                raise PipelineError(f"no playfield found in {ref.path.name}; set a manual box")
            if box is not None:
                ref.boxes.update({i: box for i in missing})
        ref.frames = [i for i in frames if i in ref.boxes]
    return ctx


@step(
    "grid_sample",
    "Grid sample",
    "video",
    "Read each cell of the playfield into a binary matrix.",
    {
        "rows": Param("int", 20),
        "cols": Param("int", 10),
        "threshold": Param("int", 50, "Brightness above which a cell counts as filled"),
        "dedupe": Param("bool", True, "Drop frames identical to the previous one"),
        "next_box": Param("str", "", "Optional next-piece preview box 'x,y,w,h' (2x4 cells)"),
    },
)
def grid_step(ctx: Context, p: dict, env: Env) -> Context:
    _need_videos(ctx, "Grid sample")
    next_box = _parse_box(p["next_box"])
    cols: dict[str, list] = {k: [] for k in ("video", "frame", "timestamp", "state", "next_piece")}
    for ref in ctx.videos:
        prev = None
        for idx, frame in V.iter_frames(ref, ref.frames):
            box = ref.boxes.get(idx)
            if box is None:
                continue
            grid = V.grid_sample(frame, box, p["rows"], p["cols"], p["threshold"])
            if p["dedupe"] and prev is not None and np.array_equal(grid, prev):
                continue
            prev = grid
            nxt = None
            if next_box:
                preview = V.grid_sample(frame, next_box, 2, 4, p["threshold"])
                found = V.identify(np.argwhere(preview))
                nxt = found[0] if found else None
            cols["video"].append(ref.path.name)
            cols["frame"].append(idx)
            cols["timestamp"].append(round(idx / ref.fps, 3))
            cols["state"].append(grid.ravel().tolist())
            cols["next_piece"].append(nxt)
    schema = pa.schema(
        [("video", pa.string()), ("frame", pa.int32()), ("timestamp", pa.float32()),
         ("state", pa.list_(pa.uint8())), ("next_piece", pa.string())]
    )
    return Context(table=pa.table(cols, schema=schema), videos=ctx.videos, files=ctx.files)


@step(
    "infer_actions",
    "Infer actions",
    "video",
    "Diff consecutive board states to label each transition (spawn, left, right, down, rotate, clear).",
    {"rows": Param("int", 20), "cols": Param("int", 10), "drop_unknown": Param("bool", False)},
)
def actions_step(ctx: Context, p: dict, env: Env) -> Context:
    t = ctx.table
    if t is None or "state" not in t.column_names:
        raise PipelineError("Infer actions needs a table with a 'state' column (run Grid sample first)")
    rows = t.to_pylist()
    out = []
    for vid, group in itertools.groupby(rows, key=lambda r: r.get("video")):
        group = list(group)
        states = [np.array(r["state"], dtype=np.uint8).reshape(p["rows"], p["cols"]) for r in group]
        for r, nxt, (action, piece) in zip(group, group[1:], V.infer_actions(states)):
            if p["drop_unknown"] and action == "unknown":
                continue
            out.append({**r, "next_state": nxt["state"], "action": action, "piece": piece})
    counts: dict[str, int] = {}
    for r in out:
        counts[r["action"]] = counts.get(r["action"], 0) + 1
    env.log(f"actions: {counts}")
    schema = t.schema.append(pa.field("next_state", pa.list_(pa.uint8()))).append(
        pa.field("action", pa.string())
    ).append(pa.field("piece", pa.string()))
    return Context(table=pa.Table.from_pylist(out, schema=schema), videos=ctx.videos, files=ctx.files)


@step(
    "arithmetic_synthetic",
    "Synthetic arithmetic",
    "source",
    "Generate math problems with exact answers.",
    {
        "count": Param("int", 10000, "Number of problems"),
        "max_operand": Param("int", 100),
        "operators": Param("str", "+-*", "Any of + - * /"),
        "terms": Param("int", 2, "Operands per problem (2-5)"),
        "seed": Param("int", 0),
    },
    source=True,
)
def arithmetic_step(ctx: Context, p: dict, env: Env) -> Context:
    ops = [o for o in p["operators"] if o in "+-*/"]
    if not ops:
        raise PipelineError("operators must include at least one of + - * /")
    if not 2 <= p["terms"] <= 5 or not 1 <= p["count"] <= 5_000_000:
        raise PipelineError("terms must be 2-5 and count 1-5000000")
    rng = np.random.default_rng(p["seed"])
    questions, answers = [], []
    while len(questions) < p["count"]:
        nums = rng.integers(0, p["max_operand"] + 1, size=p["terms"]).tolist()
        chosen = rng.choice(ops, size=p["terms"] - 1).tolist()
        if "/" in chosen:
            # Keep division exact: make each divisor non-zero and the dividend a multiple.
            for i, op in enumerate(chosen):
                if op == "/":
                    nums[i + 1] = max(1, nums[i + 1])
                    nums[i] = nums[i] * nums[i + 1] if i == 0 or chosen[i - 1] != "*" else nums[i]
        expr = str(nums[0]) + "".join(f" {op} {n}" for op, n in zip(chosen, nums[1:]))
        try:
            value = eval(expr, {"__builtins__": {}}, {})  # expr is built from ints and + - * / only
        except ZeroDivisionError:
            continue
        if isinstance(value, float) and not value.is_integer():
            continue
        questions.append(expr)
        answers.append(int(value))
    return Context(table=pa.table({"question": questions, "answer": pa.array(answers, pa.int64())}))
