import signal
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy.exc import OperationalError

from tests.fakes import InMemoryJobRepository, InMemoryProjectRepository, InMemoryStore
from vector_core.domain import Job, JobStatus
from vector_core.services import JobQueueService
from worker.main import Handler, Worker

T0 = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)
LEASE_SECONDS = 30


@pytest.fixture
def clock() -> list[datetime]:
    return [T0]


@pytest.fixture
def queue(store: InMemoryStore, clock: list[datetime]) -> JobQueueService:
    return JobQueueService(
        InMemoryJobRepository(store),
        InMemoryProjectRepository(store),
        default_max_attempts=5,
        now=lambda: clock[0],
        backoff_base_seconds=5.0,
        backoff_max_seconds=300.0,
    )


@pytest.fixture
def project_id(services) -> UUID:
    return services.projects.create("Alpha").id


def scope_for(queue: JobQueueService):
    @contextmanager
    def scope() -> Iterator[JobQueueService]:
        yield queue

    return scope


def make_worker(
    queue: JobQueueService,
    tmp_path: Path,
    worker_id: str,
    handlers: Mapping[str, Handler],
    sleep: Callable[[float], None] = lambda _seconds: None,
) -> Worker:
    return Worker(
        queue_scope=scope_for(queue),
        worker_id=worker_id,
        lease_seconds=LEASE_SECONDS,
        poll_interval_seconds=1.0,
        handlers=handlers,
        heartbeat_path=tmp_path / "heartbeat",
        heartbeat_interval=60.0,
        sleep=sleep,
    )


def test_run_once_completes_job(queue, project_id, tmp_path):
    calls: list[Job] = []
    worker = make_worker(queue, tmp_path, "worker-1", {"noop": calls.append})
    job = queue.enqueue(project_id, "noop")

    assert worker.run_once() is True

    assert calls[0].id == job.id
    assert queue.get(job.id).status is JobStatus.SUCCEEDED


def test_run_once_returns_false_on_empty_queue(queue, tmp_path):
    worker = make_worker(queue, tmp_path, "worker-1", {"noop": lambda job: None})

    assert worker.run_once() is False


def test_handler_exception_schedules_retry(queue, project_id, clock, tmp_path):
    def broken(job: Job) -> None:
        raise ValueError("boom")

    worker = make_worker(queue, tmp_path, "worker-1", {"broken": broken})
    job = queue.enqueue(project_id, "broken")

    assert worker.run_once() is True

    retried = queue.get(job.id)
    assert retried.status is JobStatus.PENDING
    assert retried.attempts == 1
    assert retried.last_error == "ValueError: boom"
    assert retried.available_at == clock[0] + timedelta(seconds=5)


def test_unknown_type_is_failed_not_succeeded(queue, project_id, tmp_path):
    worker = make_worker(queue, tmp_path, "worker-1", {"noop": lambda job: None})
    job = queue.enqueue(project_id, "mystery", max_attempts=1)

    assert worker.run_once() is True

    failed = queue.get(job.id)
    assert failed.status is JobStatus.FAILED
    assert failed.last_error is not None
    assert failed.last_error.startswith("UnknownJobType")


def test_shutdown_during_handler_releases_job_and_next_worker_processes_it(queue, project_id, tmp_path):
    processed: list[UUID] = []
    first: Worker

    def interrupted(job: Job) -> None:
        first.request_stop(signal.SIGTERM)

    first = make_worker(queue, tmp_path, "worker-1", {"noop": interrupted})
    second = make_worker(queue, tmp_path, "worker-2", {"noop": lambda job: processed.append(job.id)})
    job = queue.enqueue(project_id, "noop")

    assert first.run() == 0

    released = queue.get(job.id)
    assert released.status is JobStatus.PENDING
    assert released.attempts == 0

    assert second.run_once() is True
    assert processed == [job.id]
    assert queue.get(job.id).status is JobStatus.SUCCEEDED


def test_crashed_worker_job_recovered_after_lease_expiry(queue, project_id, clock, tmp_path):
    calls: list[Job] = []
    worker = make_worker(queue, tmp_path, "worker-2", {"noop": calls.append})
    job = queue.enqueue(project_id, "noop")
    queue.claim("crashed-worker", LEASE_SECONDS)

    clock[0] = T0 + timedelta(seconds=LEASE_SECONDS + 1)
    assert worker.run_once() is False
    assert queue.get(job.id).status is JobStatus.PENDING

    clock[0] = T0 + timedelta(seconds=LEASE_SECONDS + 1 + 5)
    assert worker.run_once() is True

    recovered = queue.get(job.id)
    assert recovered.status is JobStatus.SUCCEEDED
    assert recovered.attempts == 1
    assert calls[0].id == job.id


def test_run_survives_repository_error(queue, tmp_path):
    attempts = {"count": 0}
    worker: Worker

    @contextmanager
    def flaky_scope() -> Iterator[JobQueueService]:
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise OperationalError("SELECT 1", {}, Exception("database unavailable"))
        yield queue

    def stop_after_pause(_seconds: float) -> None:
        worker.request_stop()

    worker = Worker(
        queue_scope=flaky_scope,
        worker_id="worker-1",
        lease_seconds=LEASE_SECONDS,
        poll_interval_seconds=1.0,
        handlers={"noop": lambda job: None},
        heartbeat_path=tmp_path / "heartbeat",
        heartbeat_interval=60.0,
        sleep=stop_after_pause,
    )

    assert worker.run() == 0
    assert attempts["count"] == 1
