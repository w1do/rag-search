from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from tests.fakes import InMemoryJobRepository, InMemoryProjectRepository, InMemoryStore
from vector_core.domain import Conflict, JobStatus, NotFound, ValidationError
from vector_core.services import JobQueueService

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


def at(clock: list[datetime], seconds: float) -> None:
    clock[0] = T0 + timedelta(seconds=seconds)


def test_enqueue_requires_existing_project(queue):
    with pytest.raises(NotFound):
        queue.enqueue(uuid4(), "noop")


def test_enqueue_rejects_blank_type_and_bad_max_attempts(queue, project_id):
    with pytest.raises(ValidationError):
        queue.enqueue(project_id, "   ")
    with pytest.raises(ValidationError):
        queue.enqueue(project_id, "noop", max_attempts=0)


def test_enqueue_creates_pending_job(queue, project_id):
    job = queue.enqueue(project_id, "noop", payload={"source": "file"})

    assert job.status is JobStatus.PENDING
    assert job.attempts == 0
    assert job.max_attempts == 5
    assert job.payload == {"source": "file"}
    assert queue.get(job.id) == job


def test_claim_returns_oldest_available_and_leases_it(queue, project_id, clock):
    first = queue.enqueue(project_id, "noop")
    at(clock, 1)
    queue.enqueue(project_id, "noop")
    at(clock, 2)

    claimed = queue.claim("worker-1", LEASE_SECONDS)

    assert claimed.id == first.id
    assert claimed.status is JobStatus.LEASED
    assert claimed.leased_by == "worker-1"
    assert claimed.leased_until == T0 + timedelta(seconds=2 + LEASE_SECONDS)


def test_claim_skips_not_yet_available(queue, project_id, clock):
    job = queue.enqueue(project_id, "noop")
    claimed = queue.claim("worker-1", LEASE_SECONDS)
    queue.fail(claimed.id, "worker-1", "boom")

    at(clock, 1)
    assert queue.claim("worker-1", LEASE_SECONDS) is None

    at(clock, 5)
    assert queue.claim("worker-1", LEASE_SECONDS).id == job.id


def test_fail_increments_attempts_and_applies_exponential_backoff(queue, project_id, clock):
    job = queue.enqueue(project_id, "noop")

    for attempt, delay in [(1, 5), (2, 10), (3, 20)]:
        claimed = queue.claim("worker-1", LEASE_SECONDS)
        failed = queue.fail(claimed.id, "worker-1", f"boom {attempt}")

        assert failed.id == job.id
        assert failed.status is JobStatus.PENDING
        assert failed.attempts == attempt
        assert failed.last_error == f"boom {attempt}"
        assert failed.available_at == clock[0] + timedelta(seconds=delay)
        clock[0] = failed.available_at


def test_backoff_is_capped(queue):
    assert queue.backoff(1) == timedelta(seconds=5)
    assert queue.backoff(2) == timedelta(seconds=10)
    assert queue.backoff(6) == timedelta(seconds=160)
    assert queue.backoff(7) == timedelta(seconds=300)
    assert queue.backoff(20) == timedelta(seconds=300)


def test_job_becomes_failed_after_max_attempts(queue, project_id, clock):
    job = queue.enqueue(project_id, "noop", max_attempts=2)
    queue.fail(queue.claim("worker-1", LEASE_SECONDS).id, "worker-1", "first")

    clock[0] = queue.get(job.id).available_at
    failed = queue.fail(queue.claim("worker-1", LEASE_SECONDS).id, "worker-1", "second")

    assert failed.status is JobStatus.FAILED
    assert failed.attempts == 2
    assert failed.last_error == "second"
    assert queue.claim("worker-1", LEASE_SECONDS) is None


def test_expired_lease_is_requeued_with_attempt_increment(queue, project_id, clock):
    job = queue.enqueue(project_id, "noop")
    queue.claim("crashed-worker", LEASE_SECONDS)

    at(clock, LEASE_SECONDS + 1)
    assert queue.requeue_stale() == 1

    requeued = queue.get(job.id)
    assert requeued.status is JobStatus.PENDING
    assert requeued.attempts == 1
    assert requeued.last_error == "lease expired"
    assert requeued.leased_by is None
    assert requeued.available_at == clock[0] + timedelta(seconds=5)

    clock[0] = requeued.available_at
    assert queue.claim("worker-2", LEASE_SECONDS).id == job.id


def test_expired_lease_fails_after_max_attempts(queue, project_id, clock):
    job = queue.enqueue(project_id, "noop", max_attempts=1)
    queue.claim("crashed-worker", LEASE_SECONDS)

    at(clock, LEASE_SECONDS + 1)
    assert queue.requeue_stale() == 1

    failed = queue.get(job.id)
    assert failed.status is JobStatus.FAILED
    assert failed.attempts == 1
    assert failed.last_error == "lease expired"


def test_complete_by_foreign_worker_is_rejected(queue, project_id):
    job = queue.enqueue(project_id, "noop")
    queue.claim("worker-1", LEASE_SECONDS)

    with pytest.raises(Conflict):
        queue.complete(job.id, "worker-2")
    assert queue.get(job.id).status is JobStatus.LEASED

    done = queue.complete(job.id, "worker-1")
    assert done.status is JobStatus.SUCCEEDED
    assert done.leased_by is None


def test_extend_by_leasing_worker_moves_lease_forward(queue, project_id, clock):
    job = queue.enqueue(project_id, "noop")
    queue.claim("worker-1", LEASE_SECONDS)

    at(clock, 10)
    extended = queue.extend(job.id, "worker-1", 60)

    assert extended.status is JobStatus.LEASED
    assert extended.leased_by == "worker-1"
    assert extended.leased_until == T0 + timedelta(seconds=10 + 60)
    assert queue.get(job.id).leased_until == T0 + timedelta(seconds=10 + 60)


def test_extend_by_foreign_worker_is_rejected(queue, project_id):
    job = queue.enqueue(project_id, "noop")
    queue.claim("worker-1", LEASE_SECONDS)

    with pytest.raises(Conflict):
        queue.extend(job.id, "worker-2", LEASE_SECONDS)
    assert queue.get(job.id).leased_until == T0 + timedelta(seconds=LEASE_SECONDS)


def test_extend_requires_a_leased_job(queue, project_id):
    job = queue.enqueue(project_id, "noop")

    with pytest.raises(Conflict):
        queue.extend(job.id, "worker-1", LEASE_SECONDS)


def test_extend_rejects_non_positive_lease(queue, project_id):
    job = queue.enqueue(project_id, "noop")
    queue.claim("worker-1", LEASE_SECONDS)

    with pytest.raises(ValidationError):
        queue.extend(job.id, "worker-1", 0)


def test_release_keeps_attempts(queue, project_id, clock):
    job = queue.enqueue(project_id, "noop")
    queue.claim("worker-1", LEASE_SECONDS)

    released = queue.release(job.id, "worker-1")

    assert released.status is JobStatus.PENDING
    assert released.attempts == 0
    assert released.available_at == clock[0]
    assert queue.claim("worker-2", LEASE_SECONDS).id == job.id
