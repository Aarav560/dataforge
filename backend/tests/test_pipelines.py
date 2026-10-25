import numpy as np
import pytest

from app.pipelines import Env, PipelineError, run, validate
from app.pipelines import video as V
from app.pipelines.engine import linear
from app.pipelines.templates import build
from app.synthetic.render import write_video


def env(tmp_path, assets=None):
    return Env(asset_paths=assets or {}, work_dir=tmp_path, log=lambda m: None)


@pytest.mark.parametrize(
    "graph,message",
    [
        ({"nodes": []}, "no steps"),
        ({"nodes": [{"id": "a", "type": "nope"}]}, "unknown step"),
        (linear(("grid_sample", {})), "needs an input"),
        (
            {"nodes": [{"id": "a", "type": "arithmetic_synthetic"}, {"id": "b", "type": "arithmetic_synthetic"}],
             "edges": [{"source": "a", "target": "b"}]},
            "source",
        ),
        (
            {"nodes": [{"id": "a", "type": "arithmetic_synthetic"}, {"id": "b", "type": "arithmetic_synthetic"}]},
            "exactly one",
        ),
        (
            {"nodes": [{"id": "a", "type": "sample_frames"}, {"id": "b", "type": "scene_filter"}],
             "edges": [{"source": "a", "target": "b"}, {"source": "b", "target": "a"}]},
            "cycle",
        ),
        (linear(("arithmetic_synthetic", {"count": "lots"})), "count"),
        (linear(("arithmetic_synthetic", {"bogus": 1})), "no parameter"),
    ],
)
def test_validate_rejects(graph, message):
    with pytest.raises(PipelineError, match=message):
        validate(graph)


def test_arithmetic_answers_are_exact(tmp_path):
    t = run(linear(("arithmetic_synthetic", {"count": 300, "operators": "+-*/", "terms": 3})), env(tmp_path)).table
    assert t.num_rows == 300
    for row in t.to_pylist():
        assert eval(row["question"]) == row["answer"]


def test_tetris_template(tmp_path):
    graph = linear(("tetris_synthetic", {"games": 3, "max_steps": 40}))
    t = run(graph, env(tmp_path)).table
    assert t.num_rows > 0
    assert set(t.column_names) >= {"state", "piece", "next_piece", "rotation", "column", "reward"}
    assert len(t.column("state")[0].as_py()) == 200


def test_video_template_recovers_states_and_actions(tmp_path):
    (tmp_path / "a").mkdir()
    truth = write_video(tmp_path / "a" / "game.mp4", seed=11, placements=12)
    ctx = run(build("video_state_action", [7]), env(tmp_path, {7: tmp_path / "a"}))
    rows = ctx.table.to_pylist()
    # The title card is dropped and every gameplay frame is recovered.
    assert len(rows) == len(truth) - 1
    for row, (board, _) in zip(rows, truth):
        assert np.array_equal(np.array(row["state"]).reshape(20, 10), (board > 0).astype(np.uint8))
    labels = [label for _, label in truth][1:]
    assert [r["action"] for r in rows] == labels
    assert {r["piece"] for r in rows} <= set("IOTSZJL")


def test_video_steps_need_video(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "notes.txt").write_text("x")
    with pytest.raises(PipelineError, match="needs video"):
        run(build("video_state_action", [1]), env(tmp_path, {1: tmp_path / "a"}))


def test_missing_asset(tmp_path):
    with pytest.raises(PipelineError, match="not downloaded"):
        run(build("video_state_action", [99]), env(tmp_path))


def test_infer_actions_line_clear():
    before = np.zeros((20, 10), dtype=np.uint8)
    before[-1, :] = 1
    before[-2, 0] = 1
    after = np.zeros((20, 10), dtype=np.uint8)
    after[-1, 0] = 1
    assert V.infer_actions([before, after]) == [("clear", None)]


def test_merge_concatenates_parent_tables(tmp_path):
    graph = {
        "nodes": [
            {"id": "a", "type": "arithmetic_synthetic", "params": {"count": 5}},
            {"id": "b", "type": "arithmetic_synthetic", "params": {"count": 7, "seed": 1}},
            {"id": "c", "type": "infer_actions"},
        ],
        "edges": [{"source": "a", "target": "c"}, {"source": "b", "target": "c"}],
    }
    with pytest.raises(PipelineError, match="'state' column"):
        run(graph, env(tmp_path))
