import httpx

from ..config import Settings
from .arxiv import ArxivConnector
from .base import Connector, LinkRef, RawResult, extract_links
from .github import GitHubConnector
from .huggingface import HuggingFaceConnector
from .kaggle import KaggleConnector
from .semanticscholar import SemanticScholarConnector
from .youtube import YouTubeConnector

SOURCES = ("huggingface", "kaggle", "github", "youtube", "arxiv", "semanticscholar")


def build_connectors(client: httpx.AsyncClient, settings: Settings) -> dict[str, Connector]:
    return {
        "huggingface": HuggingFaceConnector(client, settings.hf_token),
        "kaggle": KaggleConnector(client, settings.kaggle_username, settings.kaggle_key),
        "github": GitHubConnector(client, settings.github_token),
        "youtube": YouTubeConnector(client, settings.youtube_api_key),
        "arxiv": ArxivConnector(client),
        "semanticscholar": SemanticScholarConnector(client, settings.s2_api_key),
    }


__all__ = ["Connector", "LinkRef", "RawResult", "SOURCES", "build_connectors", "extract_links"]
