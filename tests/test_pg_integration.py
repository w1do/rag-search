import os
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, func, inspect, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from vector_core.config import normalize_database_url
from vector_core.db.tables import ApiKeyRow, ProjectRow

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")
ALEMBIC_INI = Path(__file__).resolve().parents[1] / "alembic.ini"
APP_TABLES = {"projects", "api_keys", "sessions", "ingestion_jobs", "sources", "source_versions", "chunks"}

pytestmark = pytest.mark.skipif(not TEST_DATABASE_URL, reason="TEST_DATABASE_URL is not set")


@pytest.fixture
def pg(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[Engine, Config]]:
    """Runs against a dedicated test database: every run starts and ends with downgrade base."""
    url = normalize_database_url(TEST_DATABASE_URL)
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.chdir(ALEMBIC_INI.parent)
    config = Config(str(ALEMBIC_INI))
    engine = create_engine(url)
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    yield engine, config
    command.downgrade(config, "base")
    engine.dispose()


def test_upgrade_downgrade_upgrade_cycle(pg):
    engine, config = pg

    assert APP_TABLES <= set(inspect(engine).get_table_names())

    command.downgrade(config, "base")
    assert not APP_TABLES & set(inspect(engine).get_table_names())

    command.upgrade(config, "head")
    assert APP_TABLES <= set(inspect(engine).get_table_names())


def test_project_name_is_unique(pg):
    engine, _ = pg
    with Session(engine) as session:
        session.add(ProjectRow(id=uuid4(), name="Alpha"))
        session.commit()

    with Session(engine) as session:
        session.add(ProjectRow(id=uuid4(), name="Alpha"))
        with pytest.raises(IntegrityError):
            session.commit()


def test_project_name_must_not_be_blank(pg):
    engine, _ = pg

    with Session(engine) as session:
        session.add(ProjectRow(id=uuid4(), name="   "))
        with pytest.raises(IntegrityError):
            session.commit()


def test_key_hash_is_unique(pg):
    engine, _ = pg
    project_id = uuid4()
    with Session(engine) as session:
        session.add(ProjectRow(id=project_id, name="Alpha"))
        session.flush()
        session.add(ApiKeyRow(id=uuid4(), project_id=project_id, prefix="abcd1234", key_hash="a" * 64))
        session.commit()

    with Session(engine) as session:
        session.add(ApiKeyRow(id=uuid4(), project_id=project_id, prefix="efgh5678", key_hash="a" * 64))
        with pytest.raises(IntegrityError):
            session.commit()


def test_deleting_project_cascades_to_its_keys(pg):
    engine, _ = pg
    project_id = uuid4()
    with Session(engine) as session:
        session.add(ProjectRow(id=project_id, name="Cascade"))
        session.flush()
        session.add(ApiKeyRow(id=uuid4(), project_id=project_id, prefix="abcd1234", key_hash="b" * 64))
        session.commit()

    with Session(engine) as session:
        session.delete(session.get(ProjectRow, project_id))
        session.commit()

    with Session(engine) as session:
        remaining = session.scalar(
            select(func.count()).select_from(ApiKeyRow).where(ApiKeyRow.project_id == project_id)
        )
        assert remaining == 0
