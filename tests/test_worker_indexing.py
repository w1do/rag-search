import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from tests.fakes import InMemoryJobRepository, InMemoryProjectRepository, InMemoryStore, memory_indexing_scope
from vector_core.config import Settings
from vector_core.domain import JobStatus, SourceStatus
from vector_core.indexes.memory import InMemoryTextIndex, InMemoryVectorIndex
from vector_core.services import IndexingService, JobQueueService
from tests.fakes import build_services
from worker.handlers import build_handlers
from worker.main import Heartbeat, LeaseExtender, Worker

T0 = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)
LEASE_SECONDS = 30


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now


def _scope(queue: JobQueueService):
    @contextmanager
    def scope() -> Iterator[JobQueueService]:
        yield queue

    return scope


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def queue(store: InMemoryStore, clock: Clock) -> JobQueueService:
    return JobQueueService(
        InMemoryJobRepository(store),
        InMemoryProjectRepository(store),
        default_max_attempts=5,
        now=clock,
    )


def _wait_until(predicate, timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def test_lease_extender_pushes_leased_until_forward(store, services, queue, clock):
    project_id = services.projects.create("Alpha").id
    job = queue.enqueue(project_id, "noop")
    leased = queue.claim("worker-a", LEASE_SECONDS)
    assert leased is not None and leased.id == job.id
    first_deadline = leased.leased_until
    clock.now = T0 + timedelta(seconds=10)
    stop = threading.Event()
    extender = LeaseExtender(_scope(queue), job.id, "worker-a", LEASE_SECONDS, 0.01, stop)

    extender.start()
    try:
        assert _wait_until(lambda: queue.get(job.id).leased_until > first_deadline)
    finally:
        stop.set()
        extender.join(timeout=3.0)

    assert not extender.is_alive()
    assert queue.get(job.id).status is JobStatus.LEASED


def test_lease_extender_stops_quietly_when_lease_is_lost(store, services, queue):
    project_id = services.projects.create("Alpha").id
    job = queue.enqueue(project_id, "noop")
    queue.claim("worker-a", LEASE_SECONDS)
    queue.complete(job.id, "worker-a")
    stop = threading.Event()
    extender = LeaseExtender(_scope(queue), job.id, "worker-a", LEASE_SECONDS, 0.01, stop)

    extender.start()
    extender.join(timeout=3.0)

    assert not extender.is_alive()
    assert queue.get(job.id).status is JobStatus.SUCCEEDED


def test_heartbeat_on_directory_path_does_not_crash(tmp_path):
    stop = threading.Event()
    stop.set()

    Heartbeat(tmp_path, 0.01, stop).run()


def test_index_job_is_processed_by_worker_handlers(settings: Settings, store: InMemoryStore, tmp_path):
    text = InMemoryTextIndex()
    vector = InMemoryVectorIndex()
    services = build_services(settings, store, text_index=text, vector_index=vector)
    project = services.projects.create("Alpha")
    source = services.sources.add_text(project.id, "Notes", "alpha beta gamma")
    indexing = IndexingService(
        memory_indexing_scope(store), text, vector, _NoVectorEmbedder(), settings
    )
    worker = Worker(
        queue_scope=_scope(services.jobs),
        worker_id="worker-a",
        lease_seconds=LEASE_SECONDS,
        poll_interval_seconds=0.0,
        handlers=build_handlers(indexing),
        heartbeat_path=tmp_path / "heartbeat",
        heartbeat_interval=60.0,
        sleep=lambda _seconds: None,
    )

    assert worker.run_once() is True

    job = next(iter(store.jobs.values()))
    assert job.status is JobStatus.SUCCEEDED
    refreshed = services.sources.get(project.id, source.id)
    assert refreshed.status is SourceStatus.INDEXED
    assert refreshed.current_version == 1
    assert text.docs


def test_index_job_marks_source_failed_only_on_last_attempt(
    settings: Settings, store: InMemoryStore, tmp_path, clock: Clock, queue: JobQueueService
):
    text = InMemoryTextIndex()
    text.available = False
    vector = InMemoryVectorIndex()
    services = build_services(settings, store, text_index=text, vector_index=vector)
    project = services.projects.create("Alpha")
    source = services.sources.add_text(project.id, "Notes", "alpha beta gamma")
    job = next(iter(store.jobs.values()))
    store.jobs[job.id] = replace(job, max_attempts=2, available_at=T0, created_at=T0, updated_at=T0)
    indexing = IndexingService(
        memory_indexing_scope(store), text, vector, _NoVectorEmbedder(), settings
    )
    worker = Worker(
        queue_scope=_scope(queue),
        worker_id="worker-a",
        lease_seconds=LEASE_SECONDS,
        poll_interval_seconds=0.0,
        handlers=build_handlers(indexing),
        heartbeat_path=tmp_path / "heartbeat",
        heartbeat_interval=60.0,
        sleep=lambda _seconds: None,
    )

    assert worker.run_once() is True

    retried = store.jobs[job.id]
    assert retried.status is JobStatus.PENDING
    assert retried.attempts == 1
    pending = services.sources.get(project.id, source.id)
    assert pending.status is SourceStatus.PENDING
    assert pending.error_message
    assert pending.current_version is None

    clock.now = T0 + timedelta(hours=1)
    assert worker.run_once() is True

    assert store.jobs[job.id].status is JobStatus.FAILED
    assert services.sources.get(project.id, source.id).status is SourceStatus.FAILED


class _NoVectorEmbedder:
    dim = None

    def embed(self, texts: list[str]) -> list[list[float]] | None:
        return None
