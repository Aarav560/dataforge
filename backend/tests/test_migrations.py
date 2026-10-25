from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, inspect

from app.db import Base, _alembic_config


def test_migrations_match_models(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'm.db'}")
    with engine.begin() as conn:
        command.upgrade(_alembic_config(conn), "head")
        assert compare_metadata(MigrationContext.configure(conn), Base.metadata) == []
        assert "baseline_runs" in inspect(conn).get_table_names()
    with engine.begin() as conn:
        command.downgrade(_alembic_config(conn), "base")
        assert set(inspect(conn).get_table_names()) <= {"alembic_version"}
