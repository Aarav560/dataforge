from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import plugins, planner
from app.config import Settings, get_settings

REPO_PLUGINS = Path(__file__).resolve().parents[2] / "plugins"

GOOD = '''
from app.connectors.base import Connector, RawResult

class LocalCatalog(Connector):
    """Internal data catalog."""
    name = "catalog"
    kinds = frozenset({"dataset"})

    def __init__(self, client, settings=None):
        super().__init__(client)

    async def search(self, query, kind, limit):
        return [RawResult(source="catalog", kind="dataset", external_id="c1", url="https://catalog/c1",
                          title=f"internal {query}", license="mit")]

    async def download(self, external_id, kind, dest, max_bytes):
        (dest / "c1.csv").write_text("a\\n1\\n")
        return 4
'''


@pytest.fixture
def plugin_dir(tmp_path, monkeypatch):
    saved = dict(planner.KIND_SOURCES)
    monkeypatch.setattr(get_settings(), "plugins_dir", tmp_path)
    yield tmp_path
    planner.KIND_SOURCES.clear()
    planner.KIND_SOURCES.update(saved)
    plugins.load(force=True)


def test_loads_and_validates(plugin_dir):
    (plugin_dir / "good.py").write_text(GOOD)
    (plugin_dir / "broken.py").write_text("raise RuntimeError('boom')")
    (plugin_dir / "clash.py").write_text(GOOD.replace('name = "catalog"', 'name = "github"').replace("LocalCatalog", "Clash"))
    (plugin_dir / "badkind.py").write_text(GOOD.replace('{"dataset"}', '{"spreadsheet"}').replace('"catalog"', '"weird"'))
    found = plugins.load(force=True)
    assert found["catalog"].error is None and found["catalog"].description == "Internal data catalog."
    errors = {k: v.error for k, v in found.items() if v.error}
    assert any("boom" in e for e in errors.values())
    assert any("clashes with a built-in" in e for e in errors.values())
    assert any("kinds must be" in e for e in errors.values())
    assert "catalog" in planner.KIND_SOURCES["dataset"]


def test_plugin_used_in_search(plugin_dir):
    (plugin_dir / "good.py").write_text(GOOD)
    plugins.load(force=True)
    from app.main import app

    with TestClient(app) as client:
        sources = {s["name"]: s for s in client.get("/api/sources").json()}
        assert sources["catalog"]["origin"] == "good.py" and sources["huggingface"]["origin"] == "built-in"
        project = client.post("/api/projects", json={"goal": "classify tetris boards"}).json()
        need = project["needs"][0]
        assert "catalog" in need["queries"]


def test_repo_zenodo_plugin_loads(monkeypatch):
    saved = dict(planner.KIND_SOURCES)
    try:
        found = plugins.load(Settings(plugins_dir=REPO_PLUGINS), force=True)
        assert found["zenodo"].error is None and found["zenodo"].kinds == ["dataset"]
    finally:
        planner.KIND_SOURCES.clear()
        planner.KIND_SOURCES.update(saved)
        plugins.load(force=True)
