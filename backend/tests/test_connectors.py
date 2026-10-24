import httpx
import pytest
import respx

from app.connectors.base import DownloadTooLarge, safe_relpath
from app.connectors.github import GitHubConnector
from app.connectors.huggingface import HuggingFaceConnector
from app.connectors.kaggle import KaggleConnector


@respx.mock
async def test_huggingface_search_and_download(tmp_path):
    respx.get("https://huggingface.co/api/datasets").respond(
        json=[
            {
                "id": "user/tetris",
                "tags": ["license:mit", "format:parquet", "size_categories:1K<n<10K"],
                "cardData": {"license": "mit"},
                "downloads": 50,
                "likes": 3,
                "lastModified": "2024-01-01T00:00:00.000Z",
            },
            {"id": "user/private", "private": True},
        ]
    )
    respx.get("https://huggingface.co/api/datasets/user/tetris").respond(
        json={"siblings": [{"rfilename": ".gitattributes"}, {"rfilename": "data/train.parquet"}]}
    )
    respx.get("https://huggingface.co/datasets/user/tetris/resolve/main/data/train.parquet").respond(
        content=b"PAR1"
    )
    async with httpx.AsyncClient() as client:
        conn = HuggingFaceConnector(client)
        results = await conn.search("tetris", "dataset", 10)
        assert len(results) == 1
        r = results[0]
        assert (r.license, r.format, r.url) == ("mit", "parquet", "https://huggingface.co/datasets/user/tetris")
        written = await conn.download("user/tetris", "dataset", tmp_path, 1000)
    assert written == 4
    assert (tmp_path / "data" / "train.parquet").read_bytes() == b"PAR1"


@respx.mock
async def test_huggingface_rejects_path_traversal(tmp_path):
    respx.get("https://huggingface.co/api/models/evil/m").respond(
        json={"siblings": [{"rfilename": "../../etc/passwd"}]}
    )
    async with httpx.AsyncClient() as client:
        with pytest.raises(ValueError):
            await HuggingFaceConnector(client).download("evil/m", "model", tmp_path, 1000)


@respx.mock
async def test_download_budget(tmp_path):
    respx.get("https://api.github.com/repos/a/b/zipball").respond(content=b"x" * 100)
    async with httpx.AsyncClient() as client:
        with pytest.raises(DownloadTooLarge):
            await GitHubConnector(client).download("a/b", "code", tmp_path, 10)
    assert not any(tmp_path.iterdir())


@respx.mock
async def test_kaggle_search():
    respx.get("https://www.kaggle.com/api/v1/datasets/list").respond(
        json=[
            {
                "ref": "conlan/tetris",
                "title": "Tetris Training",
                "totalBytes": 123,
                "licenseName": "Attribution 4.0 International (CC BY 4.0)",
                "lastUpdated": "2023-10-15T18:26:53.893Z",
                "downloadCount": 98,
                "voteCount": 4,
                "usabilityRating": 0.75,
                "url": "https://www.kaggle.com/datasets/conlan/tetris",
            }
        ]
    )
    async with httpx.AsyncClient() as client:
        [r] = await KaggleConnector(client).search("tetris", "dataset", 5)
    assert (r.external_id, r.size_bytes, r.usability) == ("conlan/tetris", 123, 0.75)


async def test_kaggle_download_needs_credentials(tmp_path):
    async with httpx.AsyncClient() as client:
        with pytest.raises(RuntimeError, match="KAGGLE_USERNAME"):
            await KaggleConnector(client).download("a/b", "dataset", tmp_path, 10)


@respx.mock
async def test_github_search_adds_kind_hint():
    route = respx.get("https://api.github.com/search/repositories").respond(
        json={
            "items": [
                {
                    "full_name": "x/tetris-gym",
                    "html_url": "https://github.com/x/tetris-gym",
                    "description": "Tetris environment",
                    "stargazers_count": 40,
                    "license": {"spdx_id": "NOASSERTION"},
                    "size": 2,
                    "pushed_at": "2024-05-01T00:00:00Z",
                    "topics": ["gym"],
                }
            ]
        }
    )
    async with httpx.AsyncClient() as client:
        [r] = await GitHubConnector(client, token="t").search("tetris", "environment", 5)
    assert route.calls.last.request.url.params["q"] == "tetris environment"
    assert route.calls.last.request.headers["Authorization"] == "Bearer t"
    assert r.license is None and r.size_bytes == 2048


def test_safe_relpath():
    assert str(safe_relpath("a/b.txt")) == "a/b.txt"
    for bad in ("/etc/passwd", "../x", "a/../../x"):
        with pytest.raises(ValueError):
            safe_relpath(bad)


@respx.mock
async def test_huggingface_relaxes_multiword_query():
    def handler(request):
        q = request.url.params["search"]
        return httpx.Response(200, json=[{"id": "x/bird-species", "tags": []}] if q == "bird-species" else [])

    route = respx.get("https://huggingface.co/api/datasets").mock(side_effect=handler)
    async with httpx.AsyncClient() as client:
        results = await HuggingFaceConnector(client).search("bird species", "dataset", 5)
    assert [r.external_id for r in results] == ["x/bird-species"]
    assert [c.request.url.params["search"] for c in route.calls] == ["bird species", "bird-species"]
