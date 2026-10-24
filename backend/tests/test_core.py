"""Unit tests for the offline core: licences, planner, ranking, Tetris engine, pipelines, exports, baseline."""
import asyncio
import struct

import polars as pl
import pytest

from dataforge import baseline, licenses, planner, ranking, tfrecord
from dataforge.pipelines import TEMPLATES, topo_order
from dataforge.pipelines.nodes import n_dedupe, n_pii, n_split, n_unify
from dataforge.synthetic import tetris
from dataforge.synthetic.generators import gen_arithmetic, gen_gridworld


# ── licences ──
@pytest.mark.parametrize("lic,cls", [
    ("mit", "commercial_ok"), ("Apache-2.0", "commercial_ok"), ("CC0", "commercial_ok"), ("cc-by-4.0", "commercial_ok"),
    ("Attribution 4.0 International (CC BY 4.0)", "commercial_ok"), ("cc-by-sa-4.0", "copyleft"), ("GPL-3.0", "copyleft"),
    ("CC BY-NC-SA 2.0", "non_commercial"), ("cc-by-nd-4.0", "no_derivatives"), ("Standard YouTube License", "local_only"),
    ("other", "unknown"), (None, "unknown"),
])
def test_license_classify(lic, cls):
    assert licenses.classify(lic)[0] == cls


def test_license_compat_blocks_commercial_with_nc():
    ok, problems = licenses.compatible(["commercial_ok", "non_commercial"], "commercial")
    assert not ok and problems
    assert licenses.compatible(["commercial_ok", "non_commercial"], "research")[0]


# ── planner (rules path; no LLM) ──
@pytest.mark.parametrize("goal,domain,subject", [
    ("I want to train a Tetris AI", "game", "Tetris"),
    ("Detect plant diseases from leaf photos", "vision", "plant diseases leaf"),
    ("predict customer churn", "tabular", "customer churn"),
    ("speech recognition for Nepali", "audio", "speech recognition for Nepali"),
])
def test_rule_planner(goal, domain, subject):
    p = asyncio.run(planner.make_plan(goal, use_llm=False))
    assert p["domain"] == domain and p["subject"] == subject
    kinds = {n["kind"] for n in p["needs"]}
    assert "dataset" in kinds and "code" in kinds
    if domain == "game":
        assert {"environment", "video"} <= kinds and p["synthetic"]["generator"] == "tetris"


def test_validate_plan_drops_bad_kinds():
    p = planner.validate_plan({"subject": "x", "needs": [{"kind": "nonsense"}, {"kind": "paper", "queries": ["a"]}]})
    assert [n["kind"] for n in p["needs"]] == ["paper"] and p["needs"][0]["sources"]


# ── ranking ──
def test_relevance_prefers_matching_titles_and_dupes_flagged():
    rs = [
        {"id": "a", "kind": "dataset", "title": "Tetris gameplay dataset", "description": "board states", "url": "u1", "meta": {}},
        {"id": "b", "kind": "dataset", "title": "Cat photos", "description": "images of cats", "url": "u2", "meta": {"downloads": 10**6}},
        {"id": "c", "kind": "dataset", "title": "tetris-gameplay-dataset", "description": "", "url": "u3", "meta": {}},
    ]
    ranking.relevance(rs, "tetris gameplay data", "train a tetris ai")
    for r in rs:
        ranking.vet(r)
    a, b, c = rs
    assert a["score"] > b["score"], "popular-but-irrelevant must not outrank relevant"
    ranking.mark_duplicates(rs)
    assert (c.get("dup_of") == "a") or (a.get("dup_of") == "c")


# ── Tetris engine ──
def test_tetris_engine_invariants():
    cols = tetris.play_games(3, max_moves=150, seed=1)
    n = len(cols["game"])
    assert n > 100 and all(len(v) == n for v in cols.values())
    assert all(len(b) == 200 and set(b) <= {"0", "1"} for b in cols["board"])
    assert sum(cols["lines_cleared"]) > 0, "heuristic agent should clear lines"
    assert sum(cols["done"]) == 3


def test_tetris_line_clear():
    board = [0] * 20
    board[19] = tetris.FULL & ~0b1111  # bottom row missing cols 0-3
    masks, ph, _ = tetris.SHAPES["I"][0]  # horizontal I
    nb, lines = tetris.drop(board, masks, ph, 0)
    assert lines == 1 and nb[19] == 0


def test_other_generators():
    import operator
    ops = {"+": operator.add, "-": operator.sub, "*": operator.mul, "//": operator.floordiv}
    a = gen_arithmetic(200)
    for q, ans in zip(a["question"], a["answer"]):
        x, op, y = q.removeprefix("What is ").removesuffix("?").split()
        assert str(ops[op](int(x), int(y))) == ans
    g = gen_gridworld(300, size=6)
    assert len(g["action"]) >= 300 and set(g["action"]) <= {"up", "down", "left", "right"}


# ── pipeline nodes ──
def test_unify_groups_schemas_and_dedupe_split():
    f1 = pl.DataFrame({"a": [1, 2], "b": ["x", "y"]})
    f2 = pl.DataFrame({"a": [2, 3], "b": ["y", "z"]})
    f3 = pl.DataFrame({"q": ["zz"]})  # unrelated schema → set aside
    ctx = {"frames": [f1, f2, f3], "notes": [], "table": None}
    ctx = n_unify(ctx, {"mode": "largest_group", "min_overlap": 0.5}, None)
    assert ctx["table"].shape == (4, 2)
    ctx = n_dedupe(ctx, {}, None)
    assert ctx["table"].height == 3
    ctx = n_split(ctx, {"train": 0.6, "val": 0.2}, None)
    assert set(ctx["table"]["split"]) <= {"train", "validation", "test"}


def test_pii_redacts_but_ignores_hashes():
    df = pl.DataFrame({"t": ["mail me at a.b@example.com", "call 555-123-4567", "sha c8564d1bd3f6177221ed7e4f63"]})
    out = n_pii({"table": df, "notes": []}, {"action": "redact"}, None)["table"]["t"].to_list()
    assert "[EMAIL]" in out[0] and "[PHONE]" in out[1] and out[2].startswith("sha c8564d1bd3f6")


def test_templates_are_dags():
    for t in TEMPLATES.values():
        assert len(topo_order(t["graph"])) == len(t["graph"]["nodes"])


# ── exports & baseline ──
def test_tfrecord_framing(tmp_path):
    p = tmp_path / "x.tfrecord"
    assert tfrecord.write(p, [{"a": 1, "b": "hi", "c": 0.5}, {"a": 2, "b": "yo", "c": 1.5}]) == 2
    data = p.read_bytes()
    n, off = 0, 0
    while off < len(data):
        (length,) = struct.unpack("<Q", data[off:off + 8])
        assert struct.unpack("<I", data[off + 8:off + 12])[0] == tfrecord._masked(data[off:off + 8])
        payload = data[off + 12:off + 12 + length]
        assert struct.unpack("<I", data[off + 12 + length:off + 16 + length])[0] == tfrecord._masked(payload)
        off += 16 + length
        n += 1
    assert n == 2
    assert tfrecord.crc32c(b"123456789") == 0xE3069283  # standard CRC-32C check value


def test_baseline_beats_majority_on_learnable_data():
    import numpy as np
    rng = np.random.default_rng(0)
    x = rng.normal(size=2000)
    df = pl.DataFrame({"x": x, "noise": rng.normal(size=2000), "label": np.where(x > 0, "pos", "neg")})
    r = baseline.train(df, "label")
    assert r["task"] == "classification" and r["accuracy"] > 0.9 > r["majority_baseline"]
