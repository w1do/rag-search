import json
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from app.deps import Services
from tests.fakes import InMemoryStore
from vector_cli import main as cli
from vector_cli.client import VectorApiError, VectorClient
from vector_cli.main import app
from vector_core.domain import Source
from vector_core.services import REINDEX_PROJECT_JOB
from vector_core.services.sources import sync_origin_key

API_URL = "http://testserver"


def write(root: Path, rel: str, data: bytes) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def sources_of(store: InMemoryStore, project: UUID) -> list[Source]:
    return [s for s in store.sources.values() if s.project_id == project]


def sync_args(directory: Path, project: UUID, api_key: str, *extra: str) -> list[str]:
    return [
        "sync",
        str(directory),
        "--project",
        str(project),
        "--url",
        API_URL,
        "--api-key",
        api_key,
        *extra,
    ]


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def project(services: Services) -> UUID:
    return services.projects.create("Docs").id


@pytest.fixture
def api_key(services: Services, project: UUID) -> str:
    return services.keys.issue(project).secret


@pytest.fixture
def http(make_client, settings) -> TestClient:
    return make_client(settings)


@pytest.fixture
def use_http(monkeypatch: pytest.MonkeyPatch, http: TestClient) -> None:
    def factory(url: str, api_key: str) -> VectorClient:
        return VectorClient(url, api_key, http=http)

    monkeypatch.setattr(cli, "make_client", factory)


def test_repeated_sync_uploads_nothing_and_keeps_source_count(
    runner, tmp_path, project, api_key, store, use_http
):
    write(tmp_path, "a.md", b"alpha")
    write(tmp_path, "b.txt", b"beta")

    first = runner.invoke(app, sync_args(tmp_path, project, api_key))
    assert first.exit_code == 0, first.output
    assert "uploaded=2 unchanged=0 pruned=0 skipped=0 errors=0" in first.stdout

    second = runner.invoke(app, sync_args(tmp_path, project, api_key))
    assert second.exit_code == 0, second.output
    assert "uploaded=0 unchanged=2 pruned=0 skipped=0 errors=0" in second.stdout
    assert len(sources_of(store, project)) == 2


def test_changed_file_creates_version_two(
    runner, tmp_path, project, api_key, store, use_http
):
    write(tmp_path, "a.md", b"alpha")
    runner.invoke(app, sync_args(tmp_path, project, api_key))

    write(tmp_path, "a.md", b"alpha changed")
    result = runner.invoke(app, sync_args(tmp_path, project, api_key))

    assert result.exit_code == 0, result.output
    assert "uploaded=1 unchanged=0" in result.stdout
    root = tmp_path.resolve().as_posix()
    source = next(
        s for s in sources_of(store, project) if s.origin_key == sync_origin_key(root, "a.md")
    )
    versions = [v for v in store.versions.values() if v.source_id == source.id]
    assert sorted(v.version for v in versions) == [1, 2]


def test_exclude_patterns_skip_matching_files(
    runner, tmp_path, project, api_key, store, use_http
):
    write(tmp_path, "a.md", b"alpha")
    write(tmp_path, "report.pdf", b"%PDF-not-real")
    write(tmp_path, "drafts/c.md", b"draft")
    write(tmp_path, "keep/d.txt", b"keep")

    result = runner.invoke(
        app,
        sync_args(tmp_path, project, api_key, "--exclude", "*.pdf", "--exclude", "drafts"),
    )

    assert result.exit_code == 0, result.output
    assert "uploaded=2 unchanged=0 pruned=0 skipped=2 errors=0" in result.stdout
    root = tmp_path.resolve().as_posix()
    assert {s.origin_key for s in sources_of(store, project)} == {
        sync_origin_key(root, "a.md"),
        sync_origin_key(root, "keep/d.txt"),
    }


def test_hidden_and_unsupported_files_are_skipped(
    runner, tmp_path, project, api_key, store, use_http
):
    write(tmp_path, "a.md", b"alpha")
    write(tmp_path, ".secret.md", b"hidden")
    write(tmp_path, "img.png", b"png")
    write(tmp_path, ".hidden/x.md", b"hidden dir")

    result = runner.invoke(app, sync_args(tmp_path, project, api_key))

    assert result.exit_code == 0, result.output
    assert "uploaded=1 unchanged=0 pruned=0 skipped=2 errors=0" in result.stdout
    assert len(sources_of(store, project)) == 1


def test_prune_removes_only_missing_file_of_its_root(
    runner, tmp_path, project, api_key, store, use_http, services
):
    write(tmp_path, "a.md", b"alpha")
    write(tmp_path, "b.md", b"beta")
    runner.invoke(app, sync_args(tmp_path, project, api_key))
    other_root, _ = services.sources.sync_file(project, "other-root", "b.md", b"beta")
    manual = services.sources.add_text(project, "Manual", "manual note")
    (tmp_path / "b.md").unlink()

    result = runner.invoke(app, sync_args(tmp_path, project, api_key, "--prune"))

    assert result.exit_code == 0, result.output
    assert "pruned=1" in result.stdout
    remaining = {s.id for s in sources_of(store, project)}
    assert other_root.id in remaining
    assert manual.id in remaining
    assert len(remaining) == 3


def test_without_prune_nothing_is_deleted(
    runner, tmp_path, project, api_key, store, use_http
):
    write(tmp_path, "a.md", b"alpha")
    write(tmp_path, "b.md", b"beta")
    runner.invoke(app, sync_args(tmp_path, project, api_key))
    (tmp_path / "b.md").unlink()

    result = runner.invoke(app, sync_args(tmp_path, project, api_key))

    assert result.exit_code == 0, result.output
    assert "pruned=0" in result.stdout
    assert len(sources_of(store, project)) == 2


def test_prune_is_skipped_when_upload_fails(
    runner, tmp_path, project, api_key, store, use_http, monkeypatch
):
    write(tmp_path, "a.md", b"alpha")
    write(tmp_path, "b.md", b"beta")
    runner.invoke(app, sync_args(tmp_path, project, api_key))
    write(tmp_path, "a.md", b"alpha changed")
    (tmp_path / "b.md").unlink()

    def failing_upload(self, project_id, sync_root, rel, path):
        raise VectorApiError(500, "upload_failed", "boom")

    monkeypatch.setattr(VectorClient, "upload", failing_upload)
    result = runner.invoke(app, sync_args(tmp_path, project, api_key, "--prune"))

    assert result.exit_code == 1, result.output
    assert "prune skipped because of upload errors" in result.stderr
    assert "pruned=0" in result.stdout
    assert len(sources_of(store, project)) == 2


def test_dry_run_does_not_upload(runner, tmp_path, project, api_key, store, use_http):
    write(tmp_path, "a.md", b"alpha")

    result = runner.invoke(app, sync_args(tmp_path, project, api_key, "--dry-run"))

    assert result.exit_code == 0, result.output
    assert "would upload a.md" in result.stdout
    assert "would_upload=1" in result.stdout
    assert sources_of(store, project) == []


def test_api_error_exits_with_code_one(runner, tmp_path, api_key, use_http):
    write(tmp_path, "a.md", b"alpha")

    result = runner.invoke(app, sync_args(tmp_path, uuid4(), api_key))

    assert result.exit_code == 1
    assert "forbidden" in result.stderr


def test_missing_api_key_exits_with_code_two(runner, tmp_path, project, monkeypatch):
    monkeypatch.delenv("VECTOR_API_KEY", raising=False)

    result = runner.invoke(app, ["reindex", "--project", str(project), "--url", API_URL])

    assert result.exit_code == 2
    assert "VECTOR_API_KEY" in result.stderr


def test_sync_help_mentions_prune_removes_excluded_files(runner):
    result = runner.invoke(app, ["sync", "--help"])

    assert result.exit_code == 0
    assert "excluded" in result.stdout


def test_cli_reindex_enqueues_reindex_job(runner, project, api_key, store, use_http):
    result = runner.invoke(app, ["reindex", "--project", str(project), "--url", API_URL, "--api-key", api_key])

    assert result.exit_code == 0, result.output
    assert "reindex job:" in result.stdout
    jobs = [j for j in store.jobs.values() if j.type == REINDEX_PROJECT_JOB]
    assert len(jobs) == 1
    assert jobs[0].project_id == project


def test_cli_search_posts_project_query_and_limit(runner, project, api_key, monkeypatch):
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        seen["key"] = request.headers.get("X-Api-Key")
        return httpx.Response(
            200,
            json={
                "hits": [
                    {
                        "source_id": str(uuid4()),
                        "title": "Notes",
                        "version": 1,
                        "text": "hello there",
                        "score": 0.5,
                        "locator": {},
                    }
                ],
                "warning": None,
            },
        )

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        cli,
        "make_client",
        lambda url, key: VectorClient(
            url, key, http=httpx.Client(base_url=url, transport=transport)
        ),
    )

    result = runner.invoke(
        app,
        ["search", "hello", "--project", str(project), "--limit", "3", "--url", API_URL, "--api-key", api_key],
    )

    assert result.exit_code == 0, result.output
    assert seen["path"] == "/api/v1/search"
    assert seen["body"] == {"project_id": str(project), "query": "hello", "limit": 3}
    assert seen["key"] == api_key
    assert "Notes" in result.stdout


def test_delete_with_foreign_sync_root_is_not_found(http, project, api_key, services):
    root = "/data/mine"
    source, _ = services.sources.sync_file(project, root, "a.md", b"alpha")

    response = http.delete(
        f"/api/v1/projects/{project}/sources/{source.id}",
        params={"sync_root": "/data/foreign"},
        headers={"X-Api-Key": api_key},
    )
    assert response.status_code == 404

    response = http.delete(
        f"/api/v1/projects/{project}/sources/{source.id}",
        params={"sync_root": root},
        headers={"X-Api-Key": api_key},
    )
    assert response.status_code == 204


def test_key_of_other_project_is_forbidden(http, project, services):
    other = services.projects.create("Other").id
    other_key = services.keys.issue(other).secret

    response = http.post(
        f"/api/v1/projects/{project}/sources/sync",
        json={"sync_root": "/data/x", "files": []},
        headers={"X-Api-Key": other_key},
    )

    assert response.status_code == 403


def test_request_without_key_is_unauthorized(http, project):
    response = http.post(
        f"/api/v1/projects/{project}/sources/sync",
        json={"sync_root": "/data/x", "files": []},
    )

    assert response.status_code == 401


def test_reindex_endpoint_returns_accepted_job(http, project, api_key):
    response = http.post(
        f"/api/v1/projects/{project}/reindex", headers={"X-Api-Key": api_key}
    )

    assert response.status_code == 202
    assert response.json()["type"] == REINDEX_PROJECT_JOB
