import logging
import os
import pathlib
import signal
import socket
import sys
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager
from types import FrameType
from uuid import UUID

from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session as DbSession

from vector_core.config import Settings
from vector_core.db.engine import create_db_engine
from vector_core.db.repositories import SqlJobRepository, SqlProjectRepository
from vector_core.domain import Conflict, DomainError, Job
from vector_core.services import JobQueueService
from worker.handlers import Handler, build_default_handlers, noop_handler

HEARTBEAT = pathlib.Path("/tmp/worker-heartbeat")
HEARTBEAT_INTERVAL_SECONDS = 10.0
LEASE_EXTENDER_JOIN_TIMEOUT_SECONDS = 5.0

QueueScope = Callable[[], AbstractContextManager[JobQueueService]]

log = logging.getLogger("worker")


class ShutdownRequested(BaseException):
    """Raised inside the main thread to interrupt the current handler or poll sleep."""


DEFAULT_HANDLERS: Mapping[str, Handler] = {"noop": noop_handler}


class UnknownJobType(DomainError):
    code = "unknown_job_type"


def sql_queue_scope(engine: Engine, settings: Settings) -> QueueScope:
    @contextmanager
    def scope() -> Iterator[JobQueueService]:
        with DbSession(engine) as session:
            yield JobQueueService(
                SqlJobRepository(session),
                SqlProjectRepository(session),
                default_max_attempts=settings.job_max_attempts,
                backoff_base_seconds=settings.job_backoff_base_seconds,
                backoff_max_seconds=settings.job_backoff_max_seconds,
            )
            session.commit()

    return scope


class Heartbeat(threading.Thread):
    def __init__(self, path: pathlib.Path, interval: float, stop: threading.Event) -> None:
        super().__init__(name="heartbeat", daemon=True)
        self._path = path
        self._interval = interval
        self._stop_event = stop

    def run(self) -> None:
        while True:
            try:
                self._path.touch()
            except OSError as exc:
                log.warning("heartbeat write failed: %s", exc)
            if self._stop_event.wait(self._interval):
                return


class LeaseExtender(threading.Thread):
    def __init__(
        self,
        queue_scope: QueueScope,
        job_id: UUID,
        worker_id: str,
        lease_seconds: int,
        interval: float,
        stop: threading.Event,
    ) -> None:
        super().__init__(name=f"lease-{job_id}", daemon=True)
        self._queue_scope = queue_scope
        self._job_id = job_id
        self._worker_id = worker_id
        self._lease_seconds = lease_seconds
        self._interval = interval
        self._stop_event = stop

    def run(self) -> None:
        while not self._stop_event.wait(self._interval):
            try:
                with self._queue_scope() as queue:
                    queue.extend(self._job_id, self._worker_id, self._lease_seconds)
            except Conflict:
                log.warning("job %s lease lost", self._job_id)
                return
            except (SQLAlchemyError, DomainError):
                log.exception("could not extend lease for job %s", self._job_id)


class Worker:
    def __init__(
        self,
        queue_scope: QueueScope,
        worker_id: str,
        lease_seconds: int,
        poll_interval_seconds: float,
        handlers: Mapping[str, Handler] = DEFAULT_HANDLERS,
        heartbeat_path: pathlib.Path = HEARTBEAT,
        heartbeat_interval: float = HEARTBEAT_INTERVAL_SECONDS,
        sleep: Callable[[float], None] = time.sleep,
        lease_extend_interval: float | None = None,
    ) -> None:
        self._queue_scope = queue_scope
        self._worker_id = worker_id
        self._lease_seconds = lease_seconds
        self._poll_interval = poll_interval_seconds
        self._handlers = handlers
        self._heartbeat_path = heartbeat_path
        self._heartbeat_interval = heartbeat_interval
        self._lease_extend_interval = (
            lease_seconds / 3 if lease_extend_interval is None else lease_extend_interval
        )
        self._sleep = sleep
        self._stop = threading.Event()
        self._interruptible = False
        self._current: Job | None = None

    @property
    def stop_requested(self) -> bool:
        return self._stop.is_set()

    def request_stop(self, signum: int | None = None, frame: FrameType | None = None) -> None:
        first = not self._stop.is_set()
        self._stop.set()
        if first:
            log.info("stop requested (signal %s)", signum)
        if self._interruptible and threading.current_thread() is threading.main_thread():
            raise ShutdownRequested()

    def install_signal_handlers(self) -> None:
        signal.signal(signal.SIGTERM, self.request_stop)
        signal.signal(signal.SIGINT, self.request_stop)

    def run(self) -> int:
        heartbeat = Heartbeat(self._heartbeat_path, self._heartbeat_interval, self._stop)
        heartbeat.start()
        log.info("worker %s started", self._worker_id)
        try:
            while not self._stop.is_set():
                self._step()
        finally:
            self._stop.set()
            self._release_current()
            heartbeat.join(timeout=1.0)
        log.info("worker %s stopped", self._worker_id)
        return 0

    def _step(self) -> None:
        try:
            if self.run_once():
                return
        except ShutdownRequested:
            return
        except Conflict as exc:
            log.warning("job lease lost: %s", exc)
        except (SQLAlchemyError, DomainError):
            log.exception("worker iteration failed")
        finally:
            self._interruptible = False
        self._pause()

    def _pause(self) -> None:
        self._interruptible = True
        try:
            self._sleep(self._poll_interval)
        except ShutdownRequested:
            pass
        finally:
            self._interruptible = False

    def run_once(self) -> bool:
        """Processes at most one job; returns False when the queue was empty."""
        with self._queue_scope() as queue:
            requeued = queue.requeue_stale()
        if requeued:
            log.info("requeued %d stale jobs", requeued)
        if self._stop.is_set():
            return True
        with self._queue_scope() as queue:
            job = queue.claim(self._worker_id, self._lease_seconds)
        if job is None:
            return False
        # `_current` stays set while the handler runs: on ShutdownRequested the job
        # propagates to run(), which releases it back to pending.
        self._current = job
        error: str | None = None
        with self._lease_extender(job):
            try:
                self._interruptible = True
                self._handle(job)
            except Exception as exc:  # noqa: BLE001 - handler failures are recorded on the job
                log.exception("job %s (%s) failed", job.id, job.type)
                error = f"{type(exc).__name__}: {exc}"[:2000]
            finally:
                self._interruptible = False
        with self._queue_scope() as queue:
            if error is None:
                queue.complete(job.id, self._worker_id)
            else:
                queue.fail(job.id, self._worker_id, error)
        self._current = None
        return True

    @contextmanager
    def _lease_extender(self, job: Job) -> Iterator[None]:
        stop = threading.Event()
        extender = LeaseExtender(
            self._queue_scope,
            job.id,
            self._worker_id,
            self._lease_seconds,
            self._lease_extend_interval,
            stop,
        )
        extender.start()
        try:
            yield
        finally:
            stop.set()
            extender.join(timeout=LEASE_EXTENDER_JOIN_TIMEOUT_SECONDS)

    def _handle(self, job: Job) -> None:
        handler = self._handlers.get(job.type)
        if handler is None:
            raise UnknownJobType(f"no handler registered for job type {job.type!r}")
        handler(job)

    def _release_current(self) -> None:
        job = self._current
        self._current = None
        if job is None:
            return
        try:
            with self._queue_scope() as queue:
                queue.release(job.id, self._worker_id)
            log.info("released job %s back to pending", job.id)
        except (DomainError, SQLAlchemyError) as exc:
            log.warning("could not release job %s: %s", job.id, exc)


def main(handlers: Mapping[str, Handler] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = Settings.from_env()
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL is not set")
    engine = create_db_engine(settings.database_url)
    try:
        worker = Worker(
            queue_scope=sql_queue_scope(engine, settings),
            worker_id=os.environ.get("WORKER_ID") or f"{socket.gethostname()}:{os.getpid()}",
            lease_seconds=settings.job_lease_seconds,
            poll_interval_seconds=settings.job_poll_interval_seconds,
            handlers=handlers if handlers is not None else build_default_handlers(engine, settings),
            heartbeat_path=pathlib.Path(os.environ.get("WORKER_HEARTBEAT_PATH", str(HEARTBEAT))),
        )
        worker.install_signal_handlers()
        return worker.run()
    finally:
        engine.dispose()


if __name__ == "__main__":
    sys.exit(main())
