import os
import tempfile
from pathlib import Path

_tmp = Path(tempfile.mkdtemp(prefix="dataforge-test-"))
# TEST_DATABASE_URL runs the suite against another database, e.g. Postgres.
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or f"sqlite:///{_tmp / 'test.db'}"
os.environ["DATA_DIR"] = str(_tmp / "data")
os.environ["REDIS_URL"] = ""
os.environ["ANTHROPIC_API_KEY"] = ""
os.environ["DATAFORGE_EMBEDDINGS"] = "hashing"
os.environ["INDEX_AUTO_SYNC"] = "false"
os.environ["AUTO_MIGRATE"] = "false"
os.environ["PLUGINS_DIR"] = str(_tmp / "no-plugins")

import pytest  # noqa: E402

from app.connectors.base import Connector, RawResult  # noqa: E402
from app.db import Base, engine, init_db  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_db():
    init_db()
    yield
    Base.metadata.drop_all(engine)


class FakeConnector(Connector):
    def __init__(self, name, kinds, results=None, fail=False, payload=b"hello"):
        self.name = name
        self.kinds = frozenset(kinds)
        self.results = results or []
        self.fail = fail
        self.payload = payload
        self.searched: list[tuple[str, str]] = []

    async def search(self, query, kind, limit):
        self.searched.append((query, kind))
        if self.fail:
            raise RuntimeError(f"{self.name} is down")
        return [r for r in self.results if r.kind == kind][:limit]

    async def download(self, external_id, kind, dest, max_bytes):
        if self.fail:
            raise RuntimeError("download failed")
        (dest / "file.bin").write_bytes(self.payload)
        return len(self.payload)


def raw(source, ext_id, kind="dataset", **kw):
    defaults = dict(url=f"https://example.com/{ext_id}", title=ext_id)
    defaults.update(kw)
    return RawResult(source=source, kind=kind, external_id=ext_id, **defaults)
