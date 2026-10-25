import socket

import httpx
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import respx
from PIL import Image

from app.analytics import class_imbalance, compute_stats, diff
from app.cards import combined_rights, render
from app.pipelines import Env, PipelineError, run
from app.pipelines.engine import Context, linear
from app.pipelines.steps_web import check_public_url, find_pii, html_to_text, redact
from app.pipelines.templates import build


def env(tmp_path, assets=None):
    return Env(asset_paths=assets or {}, work_dir=tmp_path, log=lambda m: None)


def table_step(step_type: str, table: pa.Table, params: dict, tmp_path):
    from app.pipelines import REGISTRY

    spec = REGISTRY[step_type]
    coerced = {k: p.coerce(k, params.get(k)) for k, p in spec.params.items()}
    return spec.run(Context(table=table), coerced, env(tmp_path)).table


def test_csv_unified_template(tmp_path):
    root = tmp_path / "a"
    root.mkdir()
    (root / "one.csv").write_text("text,label\nhello,a\n  hello ,a\nworld,b\n,b\n")
    (root / "two.jsonl").write_text('{"text": "again", "label": "a", "extra": 1}\n')
    t = run(build("csv_unified", [1]), env(tmp_path, {1: root})).table
    rows = t.to_pylist()
    # Blank text dropped; "hello" deduped (normalized) only within identical source_file rows.
    assert {r["text"] for r in rows} == {"hello", "world", "again"}
    assert {r["split"] for r in rows} <= {"train", "val", "test"}
    assert "source_file" in t.column_names


def test_select_filter_sample(tmp_path):
    t = pa.table({"a": [1, 2, 3, 4], "b": ["x", "Tetris", "y", "tetris!"]})
    assert table_step("select_columns", t, {"columns": "b:text"}, tmp_path).column_names == ["text"]
    assert table_step("filter_rows", t, {"expr": "a >= 3"}, tmp_path).column("a").to_pylist() == [3, 4]
    assert table_step("filter_rows", t, {"expr": "b contains tetris"}, tmp_path).num_rows == 2
    assert table_step("sample", t, {"n": 2}, tmp_path).num_rows == 2
    with pytest.raises(PipelineError, match="unknown column"):
        table_step("select_columns", t, {"columns": "nope"}, tmp_path)
    with pytest.raises(PipelineError, match="numeric"):
        table_step("filter_rows", t, {"expr": "a > big"}, tmp_path)


def test_split_stratified_and_grouped(tmp_path):
    t = pa.table({"label": ["a"] * 80 + ["b"] * 20, "game": [i // 10 for i in range(100)]})
    out = table_step("split", t, {"stratify": "label", "seed": 1}, tmp_path).to_pylist()
    for label, n in (("a", 80), ("b", 20)):
        train = sum(1 for r in out if r["label"] == label and r["split"] == "train")
        assert train == round(0.8 * n)
    grouped = table_step("split", t, {"group": "game"}, tmp_path).to_pylist()
    by_game: dict = {}
    for r in grouped:
        by_game.setdefault(r["game"], set()).add(r["split"])
    assert all(len(s) == 1 for s in by_game.values())


def test_images_labeled_template(tmp_path):
    root = tmp_path / "imgs"
    for label in ("cat", "dog"):
        (root / label).mkdir(parents=True)
        for i in range(5):
            Image.new("RGB", (8, 6)).save(root / label / f"{i}.png")
    (root / "loose.png").write_bytes(b"not an image")
    t = run(build("images_labeled", [3]), env(tmp_path, {3: root})).table
    rows = t.to_pylist()
    assert len(rows) == 10 and {r["label"] for r in rows} == {"cat", "dog"}
    assert rows[0]["width"] == 8 and rows[0]["asset_id"] == 3


def test_chunk_text(tmp_path):
    t = pa.table({"text": ["a" * 25, ""]})
    out = table_step("chunk_text", t, {"size": 10, "overlap": 2}, tmp_path).to_pylist()
    assert [len(r["text"]) for r in out] == [10, 10, 9]
    assert [r["chunk"] for r in out] == [0, 1, 2]


def test_pii():
    text = "Contact jane@example.com or (555) 123-4567; SSN 123-45-6789; card 4111 1111 1111 1111; ip 10.0.0.1"
    assert find_pii(text) == {"email": 1, "phone": 1, "ssn": 1, "credit_card": 1, "ipv4": 1}
    out = redact(text)
    assert "jane@" not in out and "4111" not in out and "[SSN]" in out
    assert find_pii("order 1234567812345678") == {}  # fails Luhn


def test_pii_step_modes(tmp_path):
    t = pa.table({"text": ["mail me: a@b.co", "clean"]})
    assert table_step("pii_scrub", t, {"mode": "drop"}, tmp_path).num_rows == 1
    assert table_step("pii_scrub", t, {"mode": "flag"}, tmp_path).column("pii").to_pylist() == ["email", ""]


def test_html_to_text():
    title, text, links = html_to_text(
        "<html><head><title>Tetris</title><style>p{}</style></head><body><nav>menu</nav>"
        "<h1>Tetris</h1><p>A puzzle <b>game</b>.</p><script>evil()</script><a href='/wiki/Go'>Go</a></body></html>"
    )
    assert title == "Tetris" and text == "Tetris\nA puzzle game.\nGo" and links == ["/wiki/Go"]


@pytest.mark.parametrize("url", ["http://localhost/x", "http://127.0.0.1/", "http://169.254.169.254/latest", "file:///etc/passwd"])
def test_crawler_blocks_non_public(url):
    with pytest.raises(PipelineError):
        check_public_url(url)


@respx.mock
def test_crawl_pages(tmp_path, monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(None, None, None, None, ("93.184.216.34", 0))])
    respx.get("https://example.org/robots.txt").respond(text="User-agent: *\nDisallow: /private\n")
    body = "<p>" + "Tetris is a puzzle video game. " * 20 + "</p>"
    respx.get("https://example.org/").respond(
        html=f"<title>Home</title>{body}<a href='/about'>about</a><a href='/private'>p</a><a href='https://other.org/'>o</a>"
    )
    respx.get("https://example.org/about").respond(html=f"<title>About</title>{body}")
    graph = linear(("crawl_pages", {"urls": "https://example.org/", "depth": 1, "delay": 0}))
    t = run(graph, env(tmp_path)).table
    assert t.column("url").to_pylist() == ["https://example.org/", "https://example.org/about"]


def test_stats_diff_and_card(tmp_path):
    a, b = tmp_path / "a.parquet", tmp_path / "b.parquet"
    pq.write_table(pa.table({"x": list(range(100)), "label": ["a"] * 90 + ["b"] * 10}), a)
    pq.write_table(pa.table({"x": list(range(50, 150)), "label": ["a"] * 50 + ["b"] * 50, "new": [1] * 100}), b)
    stats = compute_stats(a)
    label = next(c for c in stats["columns"] if c["name"] == "label")
    assert label["top"][0] == {"value": "a", "count": 90}
    assert class_imbalance(stats) == [{"column": "label", "ratio": 9.0, "most": "a", "least": "b"}]
    x = next(c for c in compute_stats(b)["columns"] if c["name"] == "x")
    assert x["kind"] == "numeric" and sum(h["count"] for h in x["histogram"]) == 100
    d = diff(a, b)
    assert d["rows"]["delta"] == 0 and d["columns"]["added"] == [{"name": "new", "type": "BIGINT"}]
    assert d["rows"]["added"] == d["rows"]["removed"] > 0

    sources = [
        {"kind": "dataset", "title": "u/tetris", "url": "https://hf.co/u/tetris", "license": "cc-by-sa-4.0",
         "license_class": "commercial", "rights": {"commercial": True, "redistribution": True, "attribution": True, "share_alike": True}},
        {"kind": "video", "title": "CTWC final", "url": "https://youtu.be/x", "license": "YouTube Standard License",
         "license_class": "local_only", "rights": {"commercial": False, "redistribution": False, "attribution": True, "share_alike": False}},
    ]
    f = {"goal": "Tetris AI", "approach": "reinforcement_learning", "version": 2, "rows": 100,
         "columns": [{"name": "x", "type": "int64"}], "sources": sources, "rights": combined_rights(sources),
         "steps": [{"type": "split", "label": "Split", "params": {"seed": 0}}], "splits": {"train": 80},
         "imbalance": class_imbalance(stats), "pii": {"email": 2}, "synthetic": False, "video_inferred": True}
    card = render(f)
    assert card.startswith("---\npretty_name: \"Tetris AI\"\nlicense: other")
    assert "- Commercial use: **no**" in card and "Share-alike terms apply from: u/tetris" in card
    assert "Class imbalance in `label`" in card and "local use only" in card and "email: 2" in card
