import hashlib
import os
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from threading import Event, Lock
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.deps import get_embedder, get_engine, get_settings, get_text_index, get_vector_index
from app.main import app
from vector_core.config import Settings, normalize_database_url
from vector_core.indexes.memory import InMemoryTextIndex, InMemoryVectorIndex
from vector_core.db.repositories import SqlSessionRepository, SqlSourceRepository
from vector_core.db.tables import JobRow, ProjectRow, SessionRow, SourceRow, SourceVersionRow
from vector_core.domain import Conflict, Session as AuthSession, utcnow
from vector_core.embeddings import NullEmbedder
from vector_core.services import IndexingService, JobQueueService
from worker.handlers import sql_indexing_scope

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")
ALEMBIC_INI = Path(__file__).resolve().parents[1] / "alembic.ini"
OWNER_EMAIL = "owner@example.test"
OWNER_PASSWORD = "owner-test-password"
MCP_TOKEN = "mcp-test-token"

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL, reason="TEST_DATABASE_URL is not set"
)


@pytest.fixture
def pg_engine() -> Iterator[Engine]:
    engine: Engine = create_engine(normalize_database_url(TEST_DATABASE_URL))
    yield engine
    engine.dispose()


@pytest.fixture
def pg_client(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, pg_engine: Engine
) -> Iterator[TestClient]:
    """Real SQL repositories over a dedicated test database; only settings and engine are wired in."""
    url = normalize_database_url(TEST_DATABASE_URL)
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.chdir(ALEMBIC_INI.parent)
    config = Config(str(ALEMBIC_INI))
    command.downgrade(config, "base")
    command.upgrade(config, "head")

    engine = pg_engine
    settings = Settings(
        database_url=url,
        email=OWNER_EMAIL,
        password=OWNER_PASSWORD,
        mcp_token=MCP_TOKEN,
        blob_dir=tmp_path / "blobs",
    )
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_engine] = lambda: engine
    app.dependency_overrides[get_text_index] = lambda: InMemoryTextIndex()
    app.dependency_overrides[get_vector_index] = lambda: InMemoryVectorIndex()
    yield TestClient(app)
    app.dependency_overrides.clear()

    command.downgrade(config, "base")


def count_rows(engine: Engine, model, **filters) -> int:
    with Session(engine) as session:
        query = select(func.count()).select_from(model)
        for column, value in filters.items():
            query = query.where(getattr(model, column) == value)
        return session.scalar(query) or 0


@pytest.fixture
def owner_headers(pg_client: TestClient) -> dict[str, str]:
    response = pg_client.post(
        "/api/auth/login", json={"email": OWNER_EMAIL, "password": OWNER_PASSWORD}
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['token']}"}


def test_key_lifecycle_over_http_with_postgres(
    pg_client: TestClient, owner_headers: dict[str, str]
):
    project_a = pg_client.post(
        "/api/projects", json={"name": "Alpha"}, headers=owner_headers
    )
    project_b = pg_client.post(
        "/api/projects", json={"name": "Beta"}, headers=owner_headers
    )
    assert project_a.status_code == 201
    assert project_b.status_code == 201
    a_id = project_a.json()["id"]
    b_id = project_b.json()["id"]

    issued = pg_client.post(f"/api/projects/{a_id}/keys", headers=owner_headers)
    assert issued.status_code == 201
    key = issued.json()
    key_headers = {"X-Api-Key": key["secret"]}

    assert (
        pg_client.get(f"/api/v1/projects/{a_id}", headers=key_headers).status_code
        == 200
    )
    assert (
        pg_client.get(f"/api/v1/projects/{b_id}", headers=key_headers).status_code
        == 403
    )

    revoked = pg_client.post(
        f"/api/projects/{a_id}/keys/{key['id']}/revoke", headers=owner_headers
    )
    assert revoked.status_code == 200
    assert (
        pg_client.get(f"/api/v1/projects/{a_id}", headers=key_headers).status_code
        == 401
    )


def test_commit_failure_is_not_reported_as_success(
    pg_client: TestClient,
    owner_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
):
    def failing_commit(self: Session) -> None:
        raise OperationalError("COMMIT", {}, Exception("commit failed"))

    failing_client = TestClient(app, raise_server_exceptions=False)
    monkeypatch.setattr(Session, "commit", failing_commit)

    response = failing_client.post(
        "/api/projects", json={"name": "Gamma"}, headers=owner_headers
    )

    assert not 200 <= response.status_code < 300


def test_source_lifecycle_over_http_with_postgres(
    pg_client: TestClient,
    owner_headers: dict[str, str],
    pg_engine: Engine,
    tmp_path: Path,
):
    project_id = pg_client.post(
        "/api/projects", json={"name": "Docs"}, headers=owner_headers
    ).json()["id"]
    base = f"/api/projects/{project_id}/sources"

    created = pg_client.post(
        f"{base}/text", json={"title": "Notes", "text": "hello"}, headers=owner_headers
    )
    assert created.status_code == 201
    source_id = created.json()["id"]
    assert created.json()["status"] == "pending"

    uploaded = pg_client.post(
        f"{base}/file",
        files={"file": ("note.md", b"# Heading", "text/markdown")},
        headers=owner_headers,
    )
    assert uploaded.status_code == 201
    file_id = uploaded.json()["id"]

    patched = pg_client.patch(
        f"{base}/{source_id}", json={"text": "v2"}, headers=owner_headers
    )
    assert patched.status_code == 200

    versions = pg_client.get(
        f"{base}/{source_id}/versions", headers=owner_headers
    ).json()
    assert [v["version"] for v in versions] == [1, 2]
    assert versions[1]["extracted_chars"] == 2

    listed = pg_client.get(base, headers=owner_headers).json()
    assert {s["id"] for s in listed} == {source_id, file_id}

    blob_root = tmp_path / "blobs" / project_id
    assert (blob_root / source_id / "1.txt").read_bytes() == b"hello"
    assert (blob_root / source_id / "2.txt").read_bytes() == b"v2"
    assert (blob_root / file_id / "1.md").read_bytes() == b"# Heading"
    assert count_rows(pg_engine, JobRow, type="index_version", status="pending") == 3

    assert (
        pg_client.delete(f"{base}/{source_id}", headers=owner_headers).status_code
        == 204
    )
    assert (
        pg_client.get(f"{base}/{source_id}", headers=owner_headers).status_code == 404
    )
    assert count_rows(pg_engine, SourceRow) == 1
    assert count_rows(pg_engine, SourceVersionRow) == 1
    assert not (blob_root / source_id).exists()
    assert (blob_root / file_id / "1.md").exists()


def test_delete_keeps_blobs_when_commit_fails_with_postgres(
    pg_client: TestClient,
    owner_headers: dict[str, str],
    pg_engine: Engine,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    project_id = pg_client.post(
        "/api/projects", json={"name": "Docs"}, headers=owner_headers
    ).json()["id"]
    base = f"/api/projects/{project_id}/sources"
    source_id = pg_client.post(
        f"{base}/text", json={"title": "N", "text": "keep"}, headers=owner_headers
    ).json()["id"]
    blob = tmp_path / "blobs" / project_id / source_id / "1.txt"
    assert blob.exists()

    def failing_commit(self: Session) -> None:
        raise OperationalError("COMMIT", {}, Exception("commit failed"))

    failing_client = TestClient(app, raise_server_exceptions=False)
    with monkeypatch.context() as patched:
        patched.setattr(Session, "commit", failing_commit)
        response = failing_client.delete(f"{base}/{source_id}", headers=owner_headers)

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "db_unavailable"
    assert count_rows(pg_engine, SourceRow, id=source_id) == 1
    assert blob.exists()

    assert (
        pg_client.delete(f"{base}/{source_id}", headers=owner_headers).status_code
        == 204
    )
    assert not blob.exists()
    assert count_rows(pg_engine, SourceRow) == 0


def test_delete_project_removes_blob_prefix_only_after_commit_with_postgres(
    pg_client: TestClient,
    owner_headers: dict[str, str],
    pg_engine: Engine,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    project_id = pg_client.post(
        "/api/projects", json={"name": "Docs"}, headers=owner_headers
    ).json()["id"]
    source_id = pg_client.post(
        f"/api/projects/{project_id}/sources/text",
        json={"title": "N", "text": "keep"},
        headers=owner_headers,
    ).json()["id"]
    blob = tmp_path / "blobs" / project_id / source_id / "1.txt"
    assert blob.exists()

    def failing_commit(self: Session) -> None:
        raise OperationalError("COMMIT", {}, Exception("commit failed"))

    failing_client = TestClient(app, raise_server_exceptions=False)
    with monkeypatch.context() as patched:
        patched.setattr(Session, "commit", failing_commit)
        response = failing_client.delete(
            f"/api/projects/{project_id}", headers=owner_headers
        )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "db_unavailable"
    assert count_rows(pg_engine, ProjectRow, id=project_id) == 1
    assert count_rows(pg_engine, SourceRow, id=source_id) == 1
    assert blob.exists()

    response = pg_client.delete(
        f"/api/projects/{project_id}", headers=owner_headers
    )

    assert response.status_code == 204
    assert count_rows(pg_engine, ProjectRow, id=project_id) == 0
    assert count_rows(pg_engine, SourceRow, id=source_id) == 0
    assert not blob.exists()


def test_blob_is_removed_when_transaction_rolls_back_with_postgres(
    pg_client: TestClient,
    owner_headers: dict[str, str],
    pg_engine: Engine,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    project_id = pg_client.post(
        "/api/projects", json={"name": "Docs"}, headers=owner_headers
    ).json()["id"]
    base = f"/api/projects/{project_id}/sources"

    def failing_enqueue(self, *args, **kwargs):
        raise Conflict("queue unavailable")

    with monkeypatch.context() as patched:
        patched.setattr(JobQueueService, "enqueue", failing_enqueue)
        response = pg_client.post(
            f"{base}/text", json={"title": "N", "text": "lost"}, headers=owner_headers
        )

    assert response.status_code == 409
    assert count_rows(pg_engine, SourceRow) == 0
    assert count_rows(pg_engine, SourceVersionRow) == 0
    project_blobs = tmp_path / "blobs" / project_id
    assert not project_blobs.exists() or not any(project_blobs.rglob("*.txt"))


def test_concurrent_edits_get_distinct_versions_with_postgres(
    pg_client: TestClient,
    owner_headers: dict[str, str],
    pg_engine: Engine,
    tmp_path: Path,
):
    project_id = pg_client.post(
        "/api/projects", json={"name": "Docs"}, headers=owner_headers
    ).json()["id"]
    base = f"/api/projects/{project_id}/sources"
    source_id = pg_client.post(
        f"{base}/text", json={"title": "N", "text": "v1"}, headers=owner_headers
    ).json()["id"]

    def edit(text: str) -> int:
        return pg_client.patch(
            f"{base}/{source_id}", json={"text": text}, headers=owner_headers
        ).status_code

    with ThreadPoolExecutor(max_workers=4) as pool:
        statuses = list(pool.map(edit, [f"edit-{i}" for i in range(4)]))

    assert statuses == [200] * 4
    versions = pg_client.get(
        f"{base}/{source_id}/versions", headers=owner_headers
    ).json()
    assert [v["version"] for v in versions] == [1, 2, 3, 4, 5]
    blob_dir = tmp_path / "blobs" / project_id / source_id
    with Session(pg_engine) as session:
        rows = session.scalars(
            select(SourceVersionRow).where(SourceVersionRow.source_id == source_id)
        ).all()
    for row in rows:
        data = (blob_dir / f"{row.version}.txt").read_bytes()
        assert hashlib.sha256(data).hexdigest() == row.content_hash
        assert data.decode() == row.extracted_text


def test_title_edit_and_reindex_lock_source_with_postgres(
    pg_client: TestClient,
    owner_headers: dict[str, str],
    pg_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
):
    project_id = pg_client.post(
        "/api/projects", json={"name": "Docs"}, headers=owner_headers
    ).json()["id"]
    base = f"/api/projects/{project_id}/sources"
    source_id = pg_client.post(
        f"{base}/text", json={"title": "Original", "text": "v1"}, headers=owner_headers
    ).json()["id"]
    with Session(pg_engine) as session:
        row = session.get(SourceRow, source_id)
        assert row is not None
        row.status = "failed"
        row.error_message = "old failure"
        session.commit()

    original_get_for_update = SqlSourceRepository.get_for_update
    first_lock_acquired = Event()
    release_title_edit = Event()
    second_lock_attempted = Event()
    lock_calls = 0
    lock_calls_guard = Lock()

    def coordinated_get_for_update(self: SqlSourceRepository, requested_id):
        nonlocal lock_calls
        with lock_calls_guard:
            lock_calls += 1
            call_number = lock_calls
        if call_number == 2:
            second_lock_attempted.set()
        source = original_get_for_update(self, requested_id)
        if call_number == 1:
            first_lock_acquired.set()
            if not release_title_edit.wait(timeout=10):
                raise TimeoutError("test did not release title edit")
        return source

    monkeypatch.setattr(
        SqlSourceRepository, "get_for_update", coordinated_get_for_update
    )

    title_future = None
    reindex_future = None
    with ThreadPoolExecutor(max_workers=2) as pool:
        try:
            title_future = pool.submit(
                pg_client.patch,
                f"{base}/{source_id}",
                json={"title": "Renamed"},
                headers=owner_headers,
            )
            assert first_lock_acquired.wait(timeout=5)
            reindex_future = pool.submit(
                pg_client.post,
                f"{base}/{source_id}/reindex",
                headers=owner_headers,
            )
            assert second_lock_attempted.wait(timeout=5)
            assert not reindex_future.done()
        finally:
            release_title_edit.set()

        assert title_future is not None
        assert reindex_future is not None
        title_response = title_future.result(timeout=10)
        reindex_response = reindex_future.result(timeout=10)

    assert title_response.status_code == 200
    assert reindex_response.status_code == 202
    assert lock_calls == 2
    source = pg_client.get(f"{base}/{source_id}", headers=owner_headers).json()
    assert source["title"] == "Renamed"
    assert source["status"] == "pending"
    assert source["error_message"] is None


def _published_version_id(engine: Engine, source_id: UUID) -> UUID:
    with Session(engine) as session:
        return session.scalars(
            select(SourceVersionRow.id).where(SourceVersionRow.source_id == source_id)
        ).one()


def test_search_is_isolated_between_projects_with_postgres(
    pg_client: TestClient, owner_headers: dict[str, str], pg_engine: Engine
):
    text_index = InMemoryTextIndex()
    vector_index = InMemoryVectorIndex()
    app.dependency_overrides[get_text_index] = lambda: text_index
    app.dependency_overrides[get_vector_index] = lambda: vector_index
    app.dependency_overrides[get_embedder] = lambda: NullEmbedder()
    settings = Settings(database_url="", email="", password="", mcp_token="")
    indexing = IndexingService(
        sql_indexing_scope(pg_engine), text_index, vector_index, NullEmbedder(), settings
    )
    alpha_id = pg_client.post(
        "/api/projects", json={"name": "Alpha"}, headers=owner_headers
    ).json()["id"]
    beta_id = pg_client.post(
        "/api/projects", json={"name": "Beta"}, headers=owner_headers
    ).json()["id"]
    for project_id, title in ((alpha_id, "Alpha doc"), (beta_id, "Beta doc")):
        source_id = UUID(
            pg_client.post(
                f"/api/projects/{project_id}/sources/text",
                json={"title": title, "text": "isolation marker"},
                headers=owner_headers,
            ).json()["id"]
        )
        indexing.index_version(
            UUID(project_id), source_id, _published_version_id(pg_engine, source_id)
        )

    key = pg_client.post(f"/api/projects/{alpha_id}/keys", headers=owner_headers)
    assert key.status_code == 201
    key_headers = {"X-Api-Key": key.json()["secret"]}

    def search(headers: dict[str, str], project_id: str):
        return pg_client.post(
            "/api/v1/search",
            json={"project_id": project_id, "query": "isolation"},
            headers=headers,
        )

    own = search(key_headers, alpha_id)
    assert own.status_code == 200
    assert [hit["title"] for hit in own.json()["hits"]] == ["Alpha doc"]
    assert search(key_headers, beta_id).status_code == 403
    owner_beta = search(owner_headers, beta_id)
    assert owner_beta.status_code == 200
    assert [hit["title"] for hit in owner_beta.json()["hits"]] == ["Beta doc"]


def test_delete_expired_removes_only_expired_sessions_with_postgres(
    pg_client: TestClient, pg_engine: Engine
):
    now = utcnow()
    with Session(pg_engine) as session:
        repo = SqlSessionRepository(session)
        repo.add(
            AuthSession(
                id=uuid4(),
                token_hash="expired-hash",
                created_at=now - timedelta(days=2),
                expires_at=now - timedelta(days=1),
            )
        )
        repo.add(
            AuthSession(
                id=uuid4(),
                token_hash="live-hash",
                created_at=now,
                expires_at=now + timedelta(days=1),
            )
        )
        session.commit()

    with Session(pg_engine) as session:
        purged = SqlSessionRepository(session).delete_expired(now)
        session.commit()

    assert purged == 1
    assert count_rows(pg_engine, SessionRow, token_hash="expired-hash") == 0
    assert count_rows(pg_engine, SessionRow, token_hash="live-hash") == 1


def test_upload_errors_do_not_persist_sources_with_postgres(
    pg_client: TestClient, owner_headers: dict[str, str], pg_engine: Engine
):
    project_id = pg_client.post(
        "/api/projects", json={"name": "Docs"}, headers=owner_headers
    ).json()["id"]
    base = f"/api/projects/{project_id}/sources"

    bad = pg_client.post(
        f"{base}/file",
        files={"file": ("tool.exe", b"MZ", "application/octet-stream")},
        headers=owner_headers,
    )
    assert bad.status_code == 422
    assert bad.json()["detail"]["code"] == "unsupported_format"

    other_project = pg_client.post(
        "/api/projects", json={"name": "Other"}, headers=owner_headers
    ).json()["id"]
    created = pg_client.post(
        f"{base}/text", json={"title": "A", "text": "a"}, headers=owner_headers
    ).json()
    foreign = pg_client.get(
        f"/api/projects/{other_project}/sources/{created['id']}", headers=owner_headers
    )
    assert foreign.status_code == 404

    assert count_rows(pg_engine, SourceRow) == 1
    assert count_rows(pg_engine, JobRow, type="index_version") == 1
