import httpx

from ..config import Settings
from .base import Connector, RawResult
from .github import GitHubConnector
from .huggingface import HuggingFaceConnector
from .kaggle import KaggleConnector

SOURCES = ("huggingface", "kaggle", "github")


def build_connectors(client: httpx.AsyncClient, settings: Settings) -> dict[str, Connector]:
    return {
        "huggingface": HuggingFaceConnector(client, settings.hf_token),
        "kaggle": KaggleConnector(client, settings.kaggle_username, settings.kaggle_key),
        "github": GitHubConnector(client, settings.github_token),
    }


__all__ = ["Connector", "RawResult", "SOURCES", "build_connectors"]
