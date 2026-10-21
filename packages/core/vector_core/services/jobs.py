from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from vector_core.config import (
    DEFAULT_JOB_BACKOFF_BASE_SECONDS,
    DEFAULT_JOB_BACKOFF_MAX_SECONDS,
    DEFAULT_JOB_MAX_ATTEMPTS,
)
from vector_core.domain import (
    Conflict,
    Job,
    JobStatus,
    NotFound,
    ValidationError,
    utcnow,
)
from vector_core.repositories import JobRepository, ProjectRepository
from vector_core.services.common import LEASE_EXPIRED_ERROR


class JobQueueService:
    def __init__(
        self,
        jobs: JobRepository,
        projects: ProjectRepository,
        default_max_attempts: int = DEFAULT_JOB_MAX_ATTEMPTS,
        now: Callable[[], datetime] = utcnow,
        backoff_base_seconds: float = DEFAULT_JOB_BACKOFF_BASE_SECONDS,
        backoff_max_seconds: float = DEFAULT_JOB_BACKOFF_MAX_SECONDS,
    ) -> None:
        self._jobs = jobs
        self._projects = projects
        self._default_max_attempts = default_max_attempts
        self._now = now
        self._backoff_base_seconds = backoff_base_seconds
        self._backoff_max_seconds = backoff_max_seconds

    def enqueue(
        self,
        project_id: UUID,
        type: str,
        payload: dict[str, Any] | None = None,
        max_attempts: int | None = None,
    ) -> Job:
        if self._projects.get(project_id) is None:
            raise NotFound("project not found")
        job_type = type.strip()
        if not job_type:
            raise ValidationError("job type must not be empty")
        attempts_limit = (
            self._default_max_attempts if max_attempts is None else max_attempts
        )
        if attempts_limit < 1:
            raise ValidationError("max_attempts must be at least 1")
        now = self._now()
        job = Job(
            id=uuid4(),
            project_id=project_id,
            type=job_type,
            payload=dict(payload or {}),
            status=JobStatus.PENDING,
            attempts=0,
            max_attempts=attempts_limit,
            available_at=now,
            created_at=now,
            updated_at=now,
        )
        self._jobs.add(job)
        return job

    def get(self, job_id: UUID) -> Job:
        job = self._jobs.get(job_id)
        if job is None:
            raise NotFound("job not found")
        return job

    def claim(self, worker_id: str, lease_seconds: int) -> Job | None:
        if lease_seconds < 1:
            raise ValidationError("lease_seconds must be at least 1")
        now = self._now()
        return self._jobs.claim_one(
            worker_id, now, now + timedelta(seconds=lease_seconds)
        )

    def complete(self, job_id: UUID, worker_id: str) -> Job:
        job = self._require_leased(job_id, worker_id)
        done = replace(
            job,
            status=JobStatus.SUCCEEDED,
            leased_until=None,
            leased_by=None,
            updated_at=self._now(),
        )
        self._save_leased(done, worker_id)
        return done

    def extend(self, job_id: UUID, worker_id: str, lease_seconds: int) -> Job:
        if lease_seconds < 1:
            raise ValidationError("lease_seconds must be at least 1")
        job = self._require_leased(job_id, worker_id)
        now = self._now()
        leased_until = now + timedelta(seconds=lease_seconds)
        if not self._jobs.extend_lease(job_id, worker_id, leased_until):
            raise Conflict("job lease lost")
        return replace(job, leased_until=leased_until, updated_at=now)

    def fail(self, job_id: UUID, worker_id: str, error: str) -> Job:
        job = self._require_leased(job_id, worker_id)
        return self._retry_or_fail(job, worker_id, error, self._now())

    def release(self, job_id: UUID, worker_id: str) -> Job:
        job = self._require_leased(job_id, worker_id)
        now = self._now()
        released = replace(
            job,
            status=JobStatus.PENDING,
            available_at=now,
            leased_until=None,
            leased_by=None,
            updated_at=now,
        )
        self._save_leased(released, worker_id)
        return released

    def requeue_stale(self, limit: int = 100) -> int:
        now = self._now()
        requeued = 0
        for job in self._jobs.lock_stale(now, limit):
            if job.leased_by is None:
                continue
            try:
                self._retry_or_fail(job, job.leased_by, LEASE_EXPIRED_ERROR, now)
            except Conflict:
                continue
            requeued += 1
        return requeued

    def backoff(self, attempts: int) -> timedelta:
        seconds = min(
            self._backoff_base_seconds * 2 ** max(attempts - 1, 0),
            self._backoff_max_seconds,
        )
        return timedelta(seconds=seconds)

    def _retry_or_fail(
        self, job: Job, worker_id: str, error: str, now: datetime
    ) -> Job:
        attempts = job.attempts + 1
        if attempts < job.max_attempts:
            status = JobStatus.PENDING
            available_at = now + self.backoff(attempts)
        else:
            status = JobStatus.FAILED
            available_at = now
        result = replace(
            job,
            status=status,
            attempts=attempts,
            available_at=available_at,
            leased_until=None,
            leased_by=None,
            last_error=error,
            updated_at=now,
        )
        self._save_leased(result, worker_id)
        return result

    def _save_leased(self, job: Job, expected_worker_id: str) -> None:
        if not self._jobs.update_leased(job, expected_worker_id):
            raise Conflict("job lease lost")

    def _require_leased(self, job_id: UUID, worker_id: str) -> Job:
        job = self.get(job_id)
        if job.status is not JobStatus.LEASED:
            raise Conflict(f"job is {job.status.value}, expected leased")
        if job.leased_by != worker_id:
            raise Conflict("job is leased by another worker")
        return job
