import logging
from uuid import uuid4

from tests.fakes import InMemoryStore, build_services
from vector_core.config import Settings
from vector_core.domain import Chunk, JobStatus
from vector_core.indexes.memory import InMemoryTextIndex, InMemoryVectorIndex
from vector_core.services import PURGE_SOURCE_JOB


def _setup(settings: Settings, store: InMemoryStore):
    text = InMemoryTextIndex()
    vector = InMemoryVectorIndex()
    services = build_services(settings, store, text_index=text, vector_index=vector)
    project = services.projects.create("Alpha")
    source = services.sources.add_text(project.id, "Notes", "hello world")
    version_id = services.sources.versions(project.id, source.id)[-1].id
    chunk = Chunk(
        id=uuid4(),
        project_id=project.id,
        source_id=source.id,
        version_id=version_id,
        ordinal=0,
        text="hello world",
    )
    text.upsert([chunk])
    vector.dim = 2
    vector.upsert([chunk], [[1.0, 0.0]])
    return services, project.id, source.id, text, vector


def test_deleting_source_clears_both_indexes(settings: Settings, store: InMemoryStore):
    services, project_id, source_id, text, vector = _setup(settings, store)

    services.sources.delete(project_id, source_id)

    assert text.docs == {}
    assert vector.points == {}
    assert services.sources.list_for_project(project_id) == []


def test_unavailable_index_on_delete_queues_purge_and_still_deletes_source(
    settings: Settings, store: InMemoryStore, caplog
):
    services, project_id, source_id, text, vector = _setup(settings, store)
    text.available = False

    with caplog.at_level(logging.WARNING, logger="vector_core.services.sources"):
        services.sources.delete(project_id, source_id)

    assert services.sources.list_for_project(project_id) == []
    assert "text index purge" in caplog.text
    purge_jobs = [job for job in store.jobs.values() if job.type == PURGE_SOURCE_JOB]
    assert len(purge_jobs) == 1
    assert purge_jobs[0].project_id == project_id
    assert purge_jobs[0].payload == {"source_id": str(source_id)}
    assert purge_jobs[0].status is JobStatus.PENDING
    assert vector.points == {}
