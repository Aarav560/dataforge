from datetime import datetime, timezone

from sqlalchemy import JSON, BigInteger, DateTime, Float, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    password_hash: Mapped[str] = mapped_column(String(300), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class AuthSession(Base):
    __tablename__ = "auth_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    # Only a hash of the token is stored; the token itself lives in the cookie.
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Team(Base):
    __tablename__ = "teams"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    members: Mapped[list["TeamMember"]] = relationship(back_populates="team", cascade="all, delete-orphan")


class TeamMember(Base):
    __tablename__ = "team_members"
    __table_args__ = (UniqueConstraint("team_id", "user_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    # owner | editor | viewer
    role: Mapped[str] = mapped_column(String(16), default="editor")

    team: Mapped[Team] = relationship(back_populates="members")
    user: Mapped[User] = relationship()


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    # Shared projects belong to a team; members get their team role on it.
    team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id", ondelete="SET NULL"), index=True)
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
    # Versions derived from another one (e.g. applied label reviews) point at it.
    parent_version_id: Mapped[int | None] = mapped_column(ForeignKey("dataset_versions.id", ondelete="SET NULL"))
    version: Mapped[int] = mapped_column(default=1)
    # Snapshot of the graph that produced this version, for reproducibility.
    graph_json: Mapped[dict] = mapped_column(JSON, default=dict)
    # running | done | error
    status: Mapped[str] = mapped_column(String(16), default="running")
    schema: Mapped[list] = mapped_column(JSON, default=list)
    row_count: Mapped[int] = mapped_column(BigInteger, default=0)
    stats: Mapped[dict] = mapped_column(JSON, default=dict)
    storage_path: Mapped[str | None] = mapped_column(Text)
    log: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[str | None] = mapped_column(Text)
    card_md: Mapped[str | None] = mapped_column(Text)
    export_paths: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class HubEntry(Base):
    """Pre-indexed metadata from the big hubs, synced in the background."""

    __tablename__ = "hub_index"
    __table_args__ = (UniqueConstraint("source", "kind", "external_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(32), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    external_id: Mapped[str] = mapped_column(String(512))
    # Serialized RawResult, so an index hit becomes a result without an API call.
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    embedding: Mapped[list] = mapped_column(JSON, default=list)
    embedding_model: Mapped[str] = mapped_column(String(64), default="")
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class QueryCache(Base):
    """Semantic cache: near-duplicate searches reuse earlier results."""

    __tablename__ = "query_cache"

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(32), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    query: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list] = mapped_column(JSON, default=list)
    embedding_model: Mapped[str] = mapped_column(String(64), default="")
    results: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)


class Export(Base):
    __tablename__ = "exports"

    id: Mapped[int] = mapped_column(primary_key=True)
    version_id: Mapped[int] = mapped_column(ForeignKey("dataset_versions.id", ondelete="CASCADE"), index=True)
    format: Mapped[str] = mapped_column(String(32))
    # queued | running | done | error
    status: Mapped[str] = mapped_column(String(16), default="queued")
    push_repo: Mapped[str | None] = mapped_column(String(200))
    private: Mapped[bool] = mapped_column(default=True)
    zip_path: Mapped[str | None] = mapped_column(Text)
    files: Mapped[int] = mapped_column(default=0)
    hub_url: Mapped[str | None] = mapped_column(Text)
    warnings: Mapped[list] = mapped_column(JSON, default=list)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ReviewItem(Base):
    """A row an auto-labeler wasn't sure about, waiting for a human."""

    __tablename__ = "review_items"
    __table_args__ = (UniqueConstraint("version_id", "row_index"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    version_id: Mapped[int] = mapped_column(ForeignKey("dataset_versions.id", ondelete="CASCADE"), index=True)
    row_index: Mapped[int]
    # What the reviewer sees: text snippet or {asset_id, path} for images.
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    label_column: Mapped[str] = mapped_column(String(64), default="label")
    proposed_label: Mapped[str | None] = mapped_column(String(200))
    confidence: Mapped[float | None] = mapped_column(Float)
    # pending | accepted | corrected | rejected
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    final_label: Mapped[str | None] = mapped_column(String(200))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class BaselineRun(Base):
    """A quick model trained on a dataset version to check it carries signal."""

    __tablename__ = "baseline_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    version_id: Mapped[int] = mapped_column(ForeignKey("dataset_versions.id", ondelete="CASCADE"), index=True)
    target: Mapped[str] = mapped_column(String(200))
    exclude: Mapped[list | None] = mapped_column(JSON)
    # queued | running | done | error
    status: Mapped[str] = mapped_column(String(16), default="queued")
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    model_path: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
