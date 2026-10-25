from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import get_settings


class Base(DeclarativeBase):
    pass


def _make_engine(url: str):
    if url.startswith("sqlite"):
        return create_engine(url, connect_args={"check_same_thread": False})
    return create_engine(url, pool_pre_ping=True)


settings = get_settings()
if settings.database_url.startswith("sqlite"):
    settings.data_dir.mkdir(parents=True, exist_ok=True)
engine = _make_engine(settings.database_url)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


BACKEND_DIR = Path(__file__).resolve().parent.parent


def _alembic_config(connection):
    from alembic.config import Config

    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    cfg.attributes["connection"] = connection
    return cfg


def init_db() -> None:
    """Bring the schema up to date: Alembic migrations, or create_all when AUTO_MIGRATE is off."""
    from . import models  # noqa: F401  register tables

    if not get_settings().auto_migrate:
        Base.metadata.create_all(engine)
        return
    from alembic import command
    from alembic.autogenerate import compare_metadata
    from alembic.runtime.migration import MigrationContext
    from sqlalchemy import inspect

    with engine.begin() as connection:
        tables = set(inspect(connection).get_table_names())
        cfg = _alembic_config(connection)
        if tables and "alembic_version" not in tables:
            # Database made before migrations existed: adopt it only if it already matches.
            diffs = compare_metadata(MigrationContext.configure(connection), Base.metadata)
            if diffs:
                raise RuntimeError(
                    "this database predates migrations and its schema differs from the current models; "
                    "back it up and recreate it (or migrate it by hand), then restart"
                )
            command.stamp(cfg, "head")
        else:
            command.upgrade(cfg, "head")


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
