import httpx
import respx

from app.connectors import extract_links
from app.connectors.arxiv import ArxivConnector
from app.connectors.semanticscholar import SemanticScholarConnector
from app.connectors.youtube import STANDARD_LICENSE, YouTubeConnector
from app.licensing import classify_license

ATOM = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>http://arxiv.org/abs/1905.01652v2</id>
    <updated>2019-05-10T00:00:00Z</updated>
    <published>2019-05-05T00:00:00Z</published>
    <title>The Game of
      Tetris in Machine Learning</title>
    <summary>We survey Tetris agents. Data at https://huggingface.co/datasets/foo/tetris-logs and
      code at https://github.com/bar/tetris-rl.git.</summary>
    <author><name>A. Author</name></author>
    <arxiv:comment>Kaggle mirror: https://www.kaggle.com/datasets/baz/tetris</arxiv:comment>
    <category term="cs.LG"/>
  </entry>
</feed>"""


def test_extract_links():
    links = extract_links(
        "see huggingface.co/datasets/a/b, https://huggingface.co/org/model-x, github.com/c/d.git and "
        "https://huggingface.co/spaces/e/f"
    )
    assert {(l.source, l.kind, l.external_id) for l in links} == {
        ("huggingface", "dataset", "a/b"),
        ("huggingface", "model", "org/model-x"),
        ("github", "code", "c/d"),
    }


@respx.mock
async def test_arxiv_parses_entries_and_links():
    route = respx.get("https://export.arxiv.org/api/query").respond(text=ATOM)
    async with httpx.AsyncClient() as client:
        [r] = await ArxivConnector(client).search("tetris reinforcement learning", "paper", 5)
    assert route.calls.last.request.url.params["search_query"] == "all:tetris AND all:reinforcement AND all:learning"
    assert r.external_id == "1905.01652v2"
    assert r.title == "The Game of Tetris in Machine Learning"
    assert {(l["source"], l["external_id"]) for l in r.meta["links"]} == {
        ("huggingface", "foo/tetris-logs"),
        ("github", "bar/tetris-rl"),
        ("kaggle", "baz/tetris"),
    }


@respx.mock
async def test_semanticscholar_search_and_pdf_fallback(tmp_path):
    respx.get("https://api.semanticscholar.org/graph/v1/paper/search").respond(
        json={"data": [{"paperId": "p1", "title": "T", "abstract": "x", "citationCount": 12,
                        "externalIds": {"ArXiv": "1905.01652"}, "openAccessPdf": None}]}
    )
    respx.get("https://api.semanticscholar.org/graph/v1/paper/p1").respond(
        json={"openAccessPdf": None, "externalIds": {"ArXiv": "1905.01652"}}
    )
    respx.get("https://arxiv.org/pdf/1905.01652").respond(content=b"%PDF")
    async with httpx.AsyncClient() as client:
        conn = SemanticScholarConnector(client, api_key="k")
        [r] = await conn.search("tetris", "paper", 3)
        assert r.downloads == 12 and r.meta["arxiv"] == "1905.01652"
        assert await conn.download("p1", "paper", tmp_path, 100) == 4


@respx.mock
async def test_youtube_data_api():
    respx.get("https://www.googleapis.com/youtube/v3/search").respond(json={"items": [{"id": {"videoId": "v1"}}]})
    respx.get("https://www.googleapis.com/youtube/v3/videos").respond(
        json={"items": [{"id": "v1", "snippet": {"title": "CTWC final", "publishedAt": "2024-01-01T00:00:00Z"},
                         "statistics": {"viewCount": "1000"}, "status": {"license": "youtube"}}]}
    )
    async with httpx.AsyncClient() as client:
        [r] = await YouTubeConnector(client, api_key="k").search("tetris", "video", 5)
    assert (r.external_id, r.downloads, r.license) == ("v1", 1000, STANDARD_LICENSE)
    assert classify_license(r.license) == "local_only"
    assert r.meta["redistributable"] is False


async def test_youtube_without_key_uses_ytdlp(monkeypatch):
    calls = []

    def fake(self, query, limit):
        calls.append((query, limit))
        return []

    monkeypatch.setattr(YouTubeConnector, "_search_ytdlp", fake)
    async with httpx.AsyncClient() as client:
        assert await YouTubeConnector(client).search("tetris", "video", 4) == []
    assert calls == [("tetris", 4)]
