from datetime import datetime, timezone

from sqlalchemy import JSON, BigInteger, DateTime, Float, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[str | None] = mapped_column(String(64))
    goal: Mapped[str] = mapped_column(Text)
    approach: Mapped[str | None] = mapped_column(String(64))
    plan_json: Mapped[dict] = mapped_column(JSON, default=dict)
    planner: Mapped[str] = mapped_column(String(32), default="heuristic")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    needs: Mapped[list["DataNeed"]] = relationship(
        back_populates="project", cascade="all, delete-orphan", order_by="DataNeed.id"
    )


class DataNeed(Base):
    __tablename__ = "data_needs"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    description: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(32), default="dataset")
    approach: Mapped[str | None] = mapped_column(String(64))
    # {source_name: [query, ...]}
    queries: Mapped[dict] = mapped_column(JSON, default=dict)
    # pending | searching | done | error
    status: Mapped[str] = mapped_column(String(16), default="pending")
    error: Mapped[str | None] = mapped_column(Text)

    project: Mapped[Project] = relationship(back_populates="needs")
    results: Mapped[list["SearchResult"]] = relationship(
        back_populates="need", cascade="all, delete-orphan"
    )


class SearchResult(Base):
    __tablename__ = "search_results"

    id: Mapped[int] = mapped_column(primary_key=True)
    need_id: Mapped[int] = mapped_column(ForeignKey("data_needs.id", ondelete="CASCADE"))
    source: Mapped[str] = mapped_column(String(32))
    kind: Mapped[str] = mapped_column(String(32))
    external_id: Mapped[str] = mapped_column(String(512))
    url: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    format: Mapped[str | None] = mapped_column(String(128))
    license: Mapped[str | None] = mapped_column(String(128))
    # commercial | non_commercial | research_only | unknown
    license_class: Mapped[str] = mapped_column(String(32), default="unknown")
    relevance: Mapped[float] = mapped_column(Float, default=0.0)
    quality: Mapped[float] = mapped_column(Float, default=0.0)
    score: Mapped[float] = mapped_column(Float, default=0.0)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    need: Mapped[DataNeed] = relationship(back_populates="results")
    assets: Mapped[list["Asset"]] = relationship(back_populates="result", cascade="all, delete-orphan")


class Asset(Base):
    __tablename__ = "assets"

    id: Mapped[int] = mapped_column(primary_key=True)
    result_id: Mapped[int] = mapped_column(ForeignKey("search_results.id", ondelete="CASCADE"))
    storage_path: Mapped[str | None] = mapped_column(Text)
    checksum: Mapped[str | None] = mapped_column(String(64))
    bytes_downloaded: Mapped[int] = mapped_column(BigInteger, default=0)
    # queued | downloading | done | error
    status: Mapped[str] = mapped_column(String(16), default="queued")
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    result: Mapped[SearchResult] = relationship(back_populates="assets")


class Pipeline(Base):
    __tablename__ = "pipelines"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(200))
    template: Mapped[str | None] = mapped_column(String(64))
    graph_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class DatasetVersion(Base):
    """One pipeline run. Every run produces a new, immutable version."""

    __tablename__ = "dataset_versions"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    pipeline_id: Mapped[int | None] = mapped_column(ForeignKey("pipelines.id", ondelete="SET NULL"))
    version: Mapped[int] = mapped_column(default=1)
    # Snapshot of the graph that produced this version, for reproducibility.
    graph_json: Mapped[dict] = mapped_column(JSON, default=dict)
    # running | done | error
    status: Mapped[str] = mapped_column(String(16), default="running")
    schema: Mapped[list] = mapped_column(JSON, default=list)
    row_count: Mapped[int] = mapped_column(BigInteger, default=0)
    storage_path: Mapped[str | None] = mapped_column(Text)
    log: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[str | None] = mapped_column(Text)
    card_md: Mapped[str | None] = mapped_column(Text)
    export_paths: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
