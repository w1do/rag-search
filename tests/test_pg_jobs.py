import os
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from vector_core.config import normalize_database_url
from vector_core.db.repositories import SqlJobRepository, SqlProjectRepository
from vector_core.db.tables import JobRow
from vector_core.domain import Conflict, JobStatus, Project, utcnow
from vector_core.services import JobQueueService

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")
ROOT = Path(__file__).resolve().parents[1]
ALEMBIC_INI = ROOT / "alembic.ini"
LEASE_SECONDS = 30

pytestmark = pytest.mark.skipif(not TEST_DATABASE_URL, reason="TEST_DATABASE_URL is not set")


@pytest.fixture
def pg(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[Engine, Config]]:
    """Runs against a dedicated test database: every run starts and ends with downgrade base."""
    url = normalize_database_url(TEST_DATABASE_URL)
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.chdir(ROOT)
    config = Config(str(ALEMBIC_INI))
    engine = create_engine(url)
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    yield engine, config
    command.downgrade(config, "base")
    engine.dispose()


def make_queue(session: Session, now=utcnow, max_attempts: int = 5) -> JobQueueService:
    return JobQueueService(
        SqlJobRepository(session),
        SqlProjectRepository(session),
        default_max_attempts=max_attempts,
        now=now,
        backoff_base_seconds=5.0,
        backoff_max_seconds=300.0,
    )


def make_project(engine: Engine) -> UUID:
    project_id = uuid4()
    with Session(engine) as session:
        SqlProjectRepository(session).add(
            Project(id=project_id, name=f"project-{project_id}", created_at=utcnow())
        )
        session.commit()
    return project_id


def test_skip_locked_two_sessions(pg):
    engine, _ = pg
    project_id = make_project(engine)
    with Session(engine) as setup:
        queue = make_queue(setup)
        first = queue.enqueue(project_id, "noop")
        second = queue.enqueue(project_id, "noop")
        setup.commit()

    with Session(engine) as a, Session(engine) as b:
        claimed_a = make_queue(a).claim("worker-a", LEASE_SECONDS)
        claimed_b = make_queue(b).claim("worker-b", LEASE_SECONDS)
        assert claimed_a.id == first.id
        assert claimed_b.id == second.id
        a.commit()
        b.commit()


def test_concurrent_workers_claim_each_job_exactly_once(pg):
    engine, _ = pg
    project_id = make_project(engine)
    with Session(engine) as setup:
        queue = make_queue(setup)
        job_ids = {queue.enqueue(project_id, "noop").id for _ in range(50)}
        setup.commit()

    claimed: list[UUID] = []
    errors: list[BaseException] = []
    lock = threading.Lock()

    def work(worker_id: str) -> None:
        try:
            while True:
                with Session(engine) as session:
                    queue = make_queue(session)
                    job = queue.claim(worker_id, LEASE_SECONDS)
                    if job is None:
                        return
                    queue.complete(job.id, worker_id)
                    session.commit()
                with lock:
                    claimed.append(job.id)
        except BaseException as exc:  # noqa: BLE001 - collected and asserted in the main thread
            errors.append(exc)

    threads = [threading.Thread(target=work, args=(f"worker-{i}",), daemon=True) for i in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    assert not any(thread.is_alive() for thread in threads)
    assert errors == []
    assert len(claimed) == len(set(claimed)) == 50
    assert set(claimed) == job_ids


def test_retry_backoff_and_failed_after_limit_pg(pg):
    engine, _ = pg
    project_id = make_project(engine)
    start = utcnow()
    clock = [start]

    with Session(engine) as session:
        job = make_queue(session, now=lambda: clock[0]).enqueue(project_id, "noop", max_attempts=2)
        session.commit()

    with Session(engine) as session:
        queue = make_queue(session, now=lambda: clock[0])
        claimed = queue.claim("worker-1", LEASE_SECONDS)
        retried = queue.fail(claimed.id, "worker-1", "first")
        session.commit()
    assert retried.status is JobStatus.PENDING
    assert retried.available_at == start + timedelta(seconds=5)

    clock[0] = start + timedelta(seconds=5)
    with Session(engine) as session:
        queue = make_queue(session, now=lambda: clock[0])
        claimed = queue.claim("worker-1", LEASE_SECONDS)
        assert claimed.id == job.id
        queue.fail(claimed.id, "worker-1", "second")
        session.commit()

    with Session(engine) as session:
        stored = make_queue(session, now=lambda: clock[0]).get(job.id)
    assert stored.status is JobStatus.FAILED
    assert stored.attempts == 2
    assert stored.last_error == "second"


def test_expired_claim_recovered_pg(pg):
    engine, _ = pg
    project_id = make_project(engine)
    start = utcnow()
    clock = [start]

    with Session(engine) as session:
        job = make_queue(session, now=lambda: clock[0]).enqueue(project_id, "noop")
        make_queue(session, now=lambda: clock[0]).claim("crashed-worker", LEASE_SECONDS)
        session.commit()

    clock[0] = start + timedelta(seconds=LEASE_SECONDS + 1)
    with Session(engine) as session:
        assert make_queue(session, now=lambda: clock[0]).requeue_stale() == 1
        session.commit()

    with Session(engine) as session:
        stored = make_queue(session, now=lambda: clock[0]).get(job.id)
    assert stored.status is JobStatus.PENDING
    assert stored.attempts == 1
    assert stored.last_error == "lease expired"


def test_lease_fencing_pg(pg):
    engine, _ = pg
    project_id = make_project(engine)
    start = utcnow()
    clock = [start]

    with Session(engine) as session:
        job = make_queue(session, now=lambda: clock[0]).enqueue(project_id, "noop")
        make_queue(session, now=lambda: clock[0]).claim("worker-1", LEASE_SECONDS)
        session.commit()

    clock[0] = start + timedelta(seconds=LEASE_SECONDS + 1)
    with Session(engine) as session:
        make_queue(session, now=lambda: clock[0]).requeue_stale()
        session.commit()

    clock[0] = start + timedelta(seconds=LEASE_SECONDS + 1 + 5)
    with Session(engine) as session:
        make_queue(session, now=lambda: clock[0]).claim("worker-2", LEASE_SECONDS)
        session.commit()

    with Session(engine) as session:
        with pytest.raises(Conflict):
            make_queue(session, now=lambda: clock[0]).complete(job.id, "worker-1")

    with Session(engine) as session:
        repo = SqlJobRepository(session)
        current = repo.get(job.id)
        succeeded = replace(current, status=JobStatus.SUCCEEDED, leased_until=None, leased_by=None)
        assert repo.update_leased(succeeded, "worker-1") is False
        assert repo.update_leased(succeeded, "worker-2") is True
        session.commit()

    with Session(engine) as session:
        assert make_queue(session).get(job.id).status is JobStatus.SUCCEEDED


def test_lease_consistency_check_constraint(pg):
    engine, _ = pg
    project_id = make_project(engine)

    with Session(engine) as session:
        session.add(
            JobRow(
                id=uuid4(),
                project_id=project_id,
                type="noop",
                status="leased",
                leased_until=None,
                leased_by=None,
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()

    with Session(engine) as session:
        session.add(
            JobRow(
                id=uuid4(),
                project_id=project_id,
                type="noop",
                status="pending",
                leased_until=utcnow(),
                leased_by="worker-1",
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()


def test_worker_process_exits_cleanly_on_sigterm(pg, tmp_path):
    heartbeat = tmp_path / "heartbeat"
    env = {
        **os.environ,
        "DATABASE_URL": normalize_database_url(TEST_DATABASE_URL),
        "PYTHONPATH": os.pathsep.join([str(ROOT / "apps" / "worker"), str(ROOT / "packages" / "core")]),
        "WORKER_ID": "subprocess-worker",
        "TYPESENSE_API_KEY": "test-typesense-key",
        "WORKER_HEARTBEAT_PATH": str(heartbeat),
        "JOB_POLL_INTERVAL_SECONDS": "0.1",
    }
    proc = subprocess.Popen(
        [sys.executable, "-m", "worker.main"],
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        deadline = time.monotonic() + 10
        while not heartbeat.exists():
            if proc.poll() is not None or time.monotonic() > deadline:
                pytest.fail("worker did not start within 10 seconds")
            time.sleep(0.05)
        proc.send_signal(signal.SIGTERM)
        returncode = proc.wait(timeout=10)
    except BaseException:
        proc.kill()
        proc.wait()
        raise
    output = proc.stdout.read()

    assert returncode == 0, output
    assert "stopped" in output
