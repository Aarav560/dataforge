from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .planner import KIND_SOURCES

Kind = Literal["dataset", "model", "environment", "code", "video", "paper"]


class ProjectCreate(BaseModel):
    goal: str = Field(min_length=3, max_length=2000)
    team_id: int | None = None


class NeedIn(BaseModel):
    description: str = Field(min_length=1, max_length=1000)
    kind: Kind = "dataset"
    queries: list[str] = Field(min_length=1, max_length=10)

    @field_validator("queries")
    @classmethod
    def _clean(cls, v: list[str]) -> list[str]:
        cleaned = [q.strip()[:200] for q in v if q.strip()]
        if not cleaned:
            raise ValueError("at least one non-empty query is required")
        return cleaned


class NeedPatch(BaseModel):
    description: str | None = Field(default=None, min_length=1, max_length=1000)
    kind: Kind | None = None
    queries: list[str] | None = Field(default=None, min_length=1, max_length=10)

    @field_validator("queries")
    @classmethod
    def _clean(cls, v: list[str] | None) -> list[str] | None:
        return None if v is None else NeedIn._clean(v)


def queries_for(kind: str, queries: list[str]) -> dict[str, list[str]]:
    return {src: list(queries) for src in KIND_SOURCES[kind]}


class NeedOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    description: str
    kind: str
    queries: dict[str, list[str]]
    status: str
    error: str | None
    result_count: int = 0


class ProjectSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    owner_id: int | None = None
    team_id: int | None = None
    goal: str
    approach: str | None
    planner: str
    created_at: datetime


class ProjectOut(ProjectSummary):
    approach_reason: str | None = None
    needs: list[NeedOut]


class SearchRequest(BaseModel):
    need_ids: list[int] | None = None


class ResultOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    need_id: int
    source: str
    kind: str
    external_id: str
    url: str
    title: str
    description: str | None
    size_bytes: int | None
    format: str | None
    license: str | None
    license_class: str
    relevance: float
    quality: float
    score: float
    meta: dict
    updated_at: datetime | None


class DownloadRequest(BaseModel):
    result_ids: list[int] = Field(min_length=1, max_length=50)


class AssetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    result_id: int
    title: str
    source: str
    storage_path: str | None
    checksum: str | None
    bytes_downloaded: int
    status: str
    error: str | None
    created_at: datetime
