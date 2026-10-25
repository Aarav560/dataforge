from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BACKEND_DIR / ".env", extra="ignore")

    database_url: str = f"sqlite:///{BACKEND_DIR / 'data' / 'dataforge.db'}"
    redis_url: str | None = None
    data_dir: Path = BACKEND_DIR / "data"

    anthropic_api_key: str | None = None
    planner_model: str = "claude-opus-5-5"

    github_token: str | None = None
    kaggle_username: str | None = None
    kaggle_key: str | None = None
    hf_token: str | None = None
    youtube_api_key: str | None = None
    s2_api_key: str | None = None

    cors_origins: list[str] = ["http://localhost:3000"]
    # Per-source result cap for each query.
    results_per_query: int = 10
    # Hard cap on a single download, in bytes.
    max_download_bytes: int = 5 * 1024**3
    # Linked resources resolved per paper search, per need.
    max_followed_links: int = 10
    # Hub index: answer searches locally when it has enough good matches.
    index_first: bool = True
    index_size_per_source: int = 5000
    index_min_hits: int = 5
    # None picks a default per embedding backend (see embeddings.default_threshold).
    index_min_similarity: float | None = None
    index_auto_sync: bool = True
    index_sync_hours: int = 24
    # Semantic query cache.
    cache_ttl_hours: int = 24
    cache_similarity: float = 0.95


@lru_cache
def get_settings() -> Settings:
    return Settings()
