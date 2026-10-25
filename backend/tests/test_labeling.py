import asyncio
import json

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.config import Settings
from app.main import app
from app.pipelines import Env, PipelineError, run
from app.pipelines.engine import linear
from test_api import _wait_version

CSV = "text\nthe cat sat\na dog barked\ncat and dog\nnothing here"


def graph(**label_params):
    return linear(("inline_rows", {"csv": CSV}),
                  ("auto_label", {"labels": "cat,dog", **label_params}))


def test_heuristic_labeling(tmp_path):
    t = run(graph(), Env({}, tmp_path, log=lambda m: None)).table
    rows = t.to_pylist()
    assert [r["label"] for r in rows] == ["cat", "dog", None, None]
    assert [r["needs_review"] for r in rows] == [True, True, True, True]  # 0.6 < 0.8 threshold
    t2 = run(graph(threshold=0.5), Env({}, tmp_path, log=lambda m: None)).table
    assert [r["needs_review"] for r in t2.to_pylist()] == [False, False, True, True]


def test_label_validation(tmp_path):
    with pytest.raises(PipelineError, match="two labels"):
        run(linear(("inline_rows", {"csv": "text\nx"}), ("auto_label", {"labels": "only"})), Env({}, tmp_path))
    with pytest.raises(PipelineError, match="asset_id and path"):
        run(linear(("inline_rows", {"csv": "text\nx"}), ("auto_label", {"labels": "a,b", "column": "image"})), Env({}, tmp_path))


class FakeAnthropic:
    calls: list = []

    def __init__(self, **kw):
        self.beta = self
        self.messages = self

    def create(self, **kw):
        FakeAnthropic.calls.append(kw)
        content = kw["messages"][0]["content"]
        indices = [int(c["text"].split()[1].rstrip(":")) for c in content if c["type"] == "text" and c["text"].startswith("Item ")]
        has_image = any(c["type"] == "image" for c in content)
        items = [{"index": i, "label": "cat" if (has_image or i % 2 == 0) else "dog", "confidence": 0.9 if i != 3 else 0.3}
                 for i in indices]

        class Block:
            type = "text"
            text = json.dumps({"items": items})

        class Resp:
            stop_reason = "end_turn"

        r = Resp()
        r.content = [Block()]
        return r


def test_claude_labeling_text_and_images(tmp_path, monkeypatch):
    import anthropic

    monkeypatch.setattr(anthropic, "Anthropic", FakeAnthropic)
    FakeAnthropic.calls = []
    s = Settings(anthropic_api_key="sk-test")
    t = run(graph(batch_size=2), Env({}, tmp_path, settings=s, log=lambda m: None)).table
    rows = t.to_pylist()
    assert [r["label"] for r in rows] == ["cat", "dog", "cat", "dog"]
    assert [r["needs_review"] for r in rows] == [False, False, False, True]
    assert {r["label_source"] for r in rows} == {"claude"}
    assert len(FakeAnthropic.calls) == 2 and FakeAnthropic.calls[0]["fallbacks"] == "default"
    schema = FakeAnthropic.calls[0]["output_config"]["format"]["schema"]
    assert schema["properties"]["items"]["items"]["properties"]["label"]["enum"] == ["cat", "dog", "unknown"]

    root = tmp_path / "imgs" / "x"
    root.mkdir(parents=True)
    Image.new("RGB", (900, 600)).save(root / "a.png")
    g = linear(("load_images", {"asset_ids": [5], "label_from": "none"}),
               ("auto_label", {"labels": "cat,dog", "column": "image"}))
    t = run(g, Env({5: tmp_path / "imgs"}, tmp_path, settings=s, log=lambda m: None)).table
    assert t.to_pylist()[0]["label"] == "cat"
    img = next(c for c in FakeAnthropic.calls[-1]["messages"][0]["content"] if c["type"] == "image")
    assert img["source"]["media_type"] == "image/jpeg"


def test_review_queue_flow():
    with TestClient(app) as client:
        pid = client.post("/api/projects", json={"goal": "pets"}).json()["id"]
        g = graph()
        pipe = client.post(f"/api/projects/{pid}/pipelines", json={"graph": g}).json()
        v1 = _wait_version(client, client.post(f"/api/pipelines/{pipe['id']}/run").json()["id"])
        assert v1["status"] == "done", v1
        queue = client.get(f"/api/versions/{v1['id']}/reviews").json()
        assert queue["labels"] == ["cat", "dog"] and queue["counts"] == {"pending": 4}
        items = {i["payload"]["text"]: i for i in queue["items"]}

        assert client.post(f"/api/versions/{v1['id']}/apply-reviews").status_code == 409
        assert client.post(f"/api/reviews/{items['the cat sat']['id']}", json={"action": "accept"}).json()["status"] == "accepted"
        assert client.post(f"/api/reviews/{items['cat and dog']['id']}", json={"action": "accept"}).status_code == 422
        client.post(f"/api/reviews/{items['cat and dog']['id']}", json={"action": "correct", "label": "dog"})
        client.post(f"/api/reviews/{items['nothing here']['id']}", json={"action": "reject"})

        assert client.post(f"/api/versions/{v1['id']}/apply-reviews").json()["reviews"] == 3
        for _ in range(200):
            versions = client.get(f"/api/projects/{pid}/versions").json()
            if versions[0]["version"] == 2 and versions[0]["status"] != "running":
                break
            asyncio.run(asyncio.sleep(0.02))
        v2 = versions[0]
        assert v2["status"] == "done" and v2["parent_version_id"] == v1["id"] and v2["row_count"] == 3
        rows = client.get(f"/api/versions/{v2['id']}/rows").json()["rows"]
        by_text = {r["text"]: r for r in rows}
        assert by_text["the cat sat"]["label_source"] == "human" and by_text["cat and dog"]["label"] == "dog"
        assert by_text["a dog barked"]["needs_review"] is True  # untouched item stays queued
        assert client.get(f"/api/versions/{v2['id']}/reviews").json()["counts"] == {"pending": 1}
        d = client.get(f"/api/versions/{v2['id']}/diff").json()
        assert d["base"]["id"] == v1["id"] and d["rows"]["delta"] == -1
