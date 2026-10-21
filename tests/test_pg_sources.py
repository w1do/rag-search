import os
from collections.abc import Iterator
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, func, inspect, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from vector_core.config import normalize_database_url
from vector_core.db.repositories import (
    SqlChunkRepository,
    SqlJobRepository,
    SqlProjectRepository,
    SqlSourceRepository,
    SqlVersionRepository,
)
from vector_core.db.tables import ChunkRow, SourceRow, SourceVersionRow
from vector_core.domain import (
    Chunk,
    Conflict,
    Project,
    Source,
    SourceKind,
    SourceStatus,
    SourceVersion,
    VersionStatus,
    utcnow,
)
from vector_core.services import JobQueueService

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")
ALEMBIC_INI = Path(__file__).resolve().parents[1] / "alembic.ini"
SOURCE_TABLES = {"sources", "source_versions", "chunks"}
LEASE_SECONDS = 30

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


def make_project(engine: Engine) -> UUID:
    project_id = uuid4()
    with Session(engine) as session:
        SqlProjectRepository(session).add(
            Project(id=project_id, name=f"project-{project_id}", created_at=utcnow())
        )
        session.commit()
    return project_id


def make_source(project_id: UUID, **changes) -> Source:
    now = utcnow()
    base = Source(
        id=uuid4(),
        project_id=project_id,
        kind=SourceKind.TEXT,
        title="Doc",
        status=SourceStatus.PENDING,
        created_at=now,
        updated_at=now,
    )
    return replace(base, **changes)


def make_version(source_id: UUID, version: int = 1, **changes) -> SourceVersion:
    base = SourceVersion(
        id=uuid4(),
        source_id=source_id,
        version=version,
        status=VersionStatus.PENDING,
        created_at=utcnow(),
    )
    return replace(base, **changes)


def make_chunk(project_id: UUID, source_id: UUID, version_id: UUID, ordinal: int, text: str) -> Chunk:
    return Chunk(
        id=uuid4(),
        project_id=project_id,
        source_id=source_id,
        version_id=version_id,
        ordinal=ordinal,
        text=text,
        locator={"offset": ordinal},
    )


def count_rows(engine: Engine, model, **filters) -> int:
    with Session(engine) as session:
        query = select(func.count()).select_from(model)
        for column, value in filters.items():
            query = query.where(getattr(model, column) == value)
        return session.scalar(query)


def seed_source_tree(engine: Engine, project_id: UUID) -> tuple[Source, SourceVersion, Chunk]:
    source = make_source(project_id, kind=SourceKind.FILE, title="Guide")
    version = make_version(source.id, status=VersionStatus.PUBLISHED, extracted_chars=5)
    chunk = make_chunk(project_id, source.id, version.id, 0, "hello")
    with Session(engine) as session:
        SqlSourceRepository(session).add(source)
        SqlVersionRepository(session).add(version)
        SqlChunkRepository(session).add_many([chunk])
        session.commit()
    return source, version, chunk


def test_source_tables_exist_after_upgrade(pg):
    engine, _ = pg

    assert SOURCE_TABLES <= set(inspect(engine).get_table_names())


def test_source_round_trip_and_lookups(pg):
    engine, _ = pg
    project_id = make_project(engine)
    source = make_source(
        project_id, origin_key="https://example.test/a", sync_root="docs", kind=SourceKind.URL
    )
    with Session(engine) as session:
        SqlSourceRepository(session).add(source)
        session.commit()

    with Session(engine) as session:
        repo = SqlSourceRepository(session)
        assert repo.get(source.id) == source
        assert repo.get_by_origin(project_id, "https://example.test/a") == source
        assert repo.get_by_origin(project_id, "missing") is None
        assert repo.list_by_project(project_id) == [source]
        assert repo.list_by_sync_root(project_id, "docs") == [source]
        assert repo.list_by_sync_root(project_id, "other") == []


def test_origin_key_is_unique_per_project(pg):
    engine, _ = pg
    project_id = make_project(engine)
    with Session(engine) as session:
        repo = SqlSourceRepository(session)
        repo.add(make_source(project_id, origin_key="same"))
        session.commit()

    with Session(engine) as session:
        repo = SqlSourceRepository(session)
        with pytest.raises(Conflict):
            repo.add(make_source(project_id, origin_key="same"))


def test_sources_without_origin_key_do_not_conflict(pg):
    engine, _ = pg
    project_id = make_project(engine)
    with Session(engine) as session:
        repo = SqlSourceRepository(session)
        repo.add(make_source(project_id))
        repo.add(make_source(project_id))
        session.commit()

    assert count_rows(engine, SourceRow, project_id=project_id) == 2


def test_source_update_and_delete(pg):
    engine, _ = pg
    project_id = make_project(engine)
    source = make_source(project_id)
    with Session(engine) as session:
        SqlSourceRepository(session).add(source)
        session.commit()

    updated = replace(source, status=SourceStatus.INDEXED, current_version=1, updated_at=utcnow())
    with Session(engine) as session:
        SqlSourceRepository(session).update(updated)
        session.commit()
    with Session(engine) as session:
        assert SqlSourceRepository(session).get(source.id) == updated

    with Session(engine) as session:
        repo = SqlSourceRepository(session)
        assert repo.delete(source.id) is True
        assert repo.delete(source.id) is False
        session.commit()


def test_next_version_and_unique_version_number(pg):
    engine, _ = pg
    project_id = make_project(engine)
    source = make_source(project_id)
    with Session(engine) as session:
        SqlSourceRepository(session).add(source)
        session.commit()

    with Session(engine) as session:
        repo = SqlVersionRepository(session)
        assert repo.next_version(source.id) == 1
        repo.add(make_version(source.id, version=1))
        session.commit()

    with Session(engine) as session:
        repo = SqlVersionRepository(session)
        assert repo.next_version(source.id) == 2
        with pytest.raises(Conflict):
            repo.add(make_version(source.id, version=1))


def test_version_round_trip_and_update(pg):
    engine, _ = pg
    project_id = make_project(engine)
    source = make_source(project_id)
    version = make_version(source.id, version=1)
    with Session(engine) as session:
        SqlSourceRepository(session).add(source)
        SqlVersionRepository(session).add(version)
        session.commit()

    updated = replace(
        version,
        status=VersionStatus.PUBLISHED,
        storage_key=f"sources/{source.id}/1",
        content_hash="a" * 64,
        extracted_text="body",
        extracted_chars=4,
        vector_indexed=True,
    )
    with Session(engine) as session:
        SqlVersionRepository(session).update(updated)
        session.commit()

    with Session(engine) as session:
        repo = SqlVersionRepository(session)
        assert repo.get(version.id) == updated
        assert repo.get_by_number(source.id, 1) == updated
        assert repo.get_by_number(source.id, 2) is None
        assert repo.list_by_source(source.id) == [updated]


def test_chunks_round_trip_ordering_and_lookup(pg):
    engine, _ = pg
    project_id = make_project(engine)
    source = make_source(project_id)
    version = make_version(source.id, status=VersionStatus.PUBLISHED)
    chunks = [make_chunk(project_id, source.id, version.id, i, f"part {i}") for i in (1, 0)]
    with Session(engine) as session:
        SqlSourceRepository(session).add(source)
        SqlVersionRepository(session).add(version)
        SqlChunkRepository(session).add_many(chunks)
        session.commit()

    with Session(engine) as session:
        repo = SqlChunkRepository(session)
        listed = repo.list_by_version(version.id)
        assert [c.ordinal for c in listed] == [0, 1]
        assert listed[0] == chunks[1]
        assert repo.get_many([chunks[0].id]) == [chunks[0]]
        assert repo.get_many([]) == []
        assert [c.id for c in repo.list_published_by_project(project_id)] == [c.id for c in listed]


def test_list_published_by_project_skips_unpublished_versions(pg):
    engine, _ = pg
    project_id = make_project(engine)
    source = make_source(project_id)
    pending = make_version(source.id, version=1, status=VersionStatus.PENDING)
    published = make_version(source.id, version=2, status=VersionStatus.PUBLISHED)
    with Session(engine) as session:
        SqlSourceRepository(session).add(source)
        SqlVersionRepository(session).add(pending)
        SqlVersionRepository(session).add(published)
        SqlChunkRepository(session).add_many(
            [
                make_chunk(project_id, source.id, pending.id, 0, "draft"),
                make_chunk(project_id, source.id, published.id, 0, "live"),
            ]
        )
        session.commit()

    with Session(engine) as session:
        texts = [c.text for c in SqlChunkRepository(session).list_published_by_project(project_id)]
        assert texts == ["live"]


def test_chunk_ordinal_is_unique_per_version(pg):
    engine, _ = pg
    project_id = make_project(engine)
    source = make_source(project_id)
    version = make_version(source.id)
    with Session(engine) as session:
        SqlSourceRepository(session).add(source)
        SqlVersionRepository(session).add(version)
        SqlChunkRepository(session).add_many([make_chunk(project_id, source.id, version.id, 0, "a")])
        session.commit()

    with Session(engine) as session, pytest.raises(Conflict):
        SqlChunkRepository(session).add_many([make_chunk(project_id, source.id, version.id, 0, "b")])


def test_delete_by_version_and_source_remove_chunks(pg):
    engine, _ = pg
    project_id = make_project(engine)
    source, version, _ = seed_source_tree(engine, project_id)
    with Session(engine) as session:
        SqlChunkRepository(session).delete_by_version(version.id)
        session.commit()
    assert count_rows(engine, ChunkRow, version_id=version.id) == 0

    with Session(engine) as session:
        SqlChunkRepository(session).add_many([make_chunk(project_id, source.id, version.id, 0, "again")])
        session.commit()
    with Session(engine) as session:
        SqlChunkRepository(session).delete_by_source(source.id)
        session.commit()
    assert count_rows(engine, ChunkRow, source_id=source.id) == 0


def test_deleting_source_cascades_to_versions_and_chunks(pg):
    engine, _ = pg
    project_id = make_project(engine)
    source, _, _ = seed_source_tree(engine, project_id)

    with Session(engine) as session:
        assert SqlSourceRepository(session).delete(source.id) is True
        session.commit()

    assert count_rows(engine, SourceVersionRow, source_id=source.id) == 0
    assert count_rows(engine, ChunkRow, source_id=source.id) == 0


def test_deleting_project_cascades_to_sources_versions_and_chunks(pg):
    engine, _ = pg
    project_id = make_project(engine)
    source, _, _ = seed_source_tree(engine, project_id)

    with Session(engine) as session:
        assert SqlProjectRepository(session).delete(project_id) is True
        session.commit()

    assert count_rows(engine, SourceVersionRow, source_id=source.id) == 0
    assert count_rows(engine, ChunkRow, project_id=project_id) == 0


def test_source_version_number_is_unique_at_database_level(pg):
    engine, _ = pg
    project_id = make_project(engine)
    source = make_source(project_id)
    with Session(engine) as session:
        SqlSourceRepository(session).add(source)
        SqlVersionRepository(session).add(make_version(source.id, version=1))
        session.commit()

    with Session(engine) as session:
        session.add(SourceVersionRow(id=uuid4(), source_id=source.id, version=1, status="pending"))
        with pytest.raises(IntegrityError):
            session.commit()


def test_extend_lease_requires_the_leasing_worker(pg):
    engine, _ = pg
    project_id = make_project(engine)
    with Session(engine) as session:
        queue = JobQueueService(SqlJobRepository(session), SqlProjectRepository(session))
        queue.enqueue(project_id, "noop")
        session.commit()

    with Session(engine) as session:
        queue = JobQueueService(SqlJobRepository(session), SqlProjectRepository(session))
        claimed = queue.claim("worker-1", LEASE_SECONDS)
        session.commit()

    new_until = utcnow() + timedelta(seconds=120)
    with Session(engine) as session:
        repo = SqlJobRepository(session)
        assert repo.extend_lease(claimed.id, "worker-2", new_until) is False
        assert repo.extend_lease(claimed.id, "worker-1", new_until) is True
        session.commit()

    with Session(engine) as session:
        assert SqlJobRepository(session).get(claimed.id).leased_until == new_until
