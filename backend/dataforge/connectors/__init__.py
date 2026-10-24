"""Connector registry (plan §3.2). Plugin system (phase 5): call `register()` with a Connector subclass."""
from __future__ import annotations

from .base import Connector, Hit
from .code import Environments, GitHubCode, GitHubRepos
from .hubs import UCI, HuggingFaceDatasets, HuggingFaceModels, Kaggle, OpenML, Zenodo
from .media import InternetArchive, Openverse, WebSearch, WikimediaCommons, YouTube
from .papers import ArXiv, OpenAlex, SemanticScholar

REGISTRY: dict[str, Connector] = {}

# Which connectors serve which kind of data need.
KIND_SOURCES: dict[str, list[str]] = {
    "dataset": ["hf_datasets", "kaggle", "openml", "zenodo", "uci", "internet_archive"],
    "model": ["hf_models", "github"],
    "environment": ["environments"],
    "paper": ["arxiv", "openalex", "semantic_scholar"],
    "video": ["youtube", "internet_archive", "wikimedia"],
    "image": ["openverse", "wikimedia"],
    "web": ["web"],
    "code": ["github_code", "github"],
}


def register(conn: Connector) -> None:
    REGISTRY[conn.name] = conn


for _c in (HuggingFaceDatasets, HuggingFaceModels, Kaggle, OpenML, UCI, Zenodo, GitHubRepos, GitHubCode, Environments,
           ArXiv, SemanticScholar, OpenAlex, YouTube, InternetArchive, Openverse, WikimediaCommons, WebSearch):
    register(_c())


def catalog() -> list[dict]:
    return [{"name": c.name, "label": c.label, "kinds": list(c.kinds), "requires": c.requires, "available": c.available()}
            for c in REGISTRY.values()]


__all__ = ["Connector", "Hit", "REGISTRY", "KIND_SOURCES", "register", "catalog"]
