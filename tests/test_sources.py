import io
import json
from dataclasses import replace
from uuid import uuid4

import httpx
import pytest
from docx import Document
from fastapi.testclient import TestClient
from pypdf import PdfWriter

from app.deps import Services
from tests.conftest import MCP_TOKEN, OWNER_EMAIL, OWNER_PASSWORD
from tests.fakes import (
    InMemoryBlobStore,
    InMemoryStore,
    build_services,
    memory_indexing_scope,
)
from vector_core.config import Settings
from vector_core.domain import (
    Chunk,
    Conflict,
    JobStatus,
    NotFound,
    PayloadTooLarge,
    ServiceUnavailable,
    SourceKind,
    SourceStatus,
    UnsupportedFormat,
    ValidationError,
    VersionStatus,
)
from vector_core.embeddings import NullEmbedder
from vector_core.indexes.memory import InMemoryTextIndex, InMemoryVectorIndex
from vector_core.services import INDEX_VERSION_JOB, IndexingService
from vector_core.services.common import TransactionHooks

SMALL_LIMIT = 64
FIRECRAWL_KEY = "fc-test-secret-key-123"


def make_docx(text: str) -> bytes:
    document = Document()
    document.add_paragraph(text)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def make_pdf() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def url_transport(handler) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


def firecrawl_settings(settings: Settings, **overrides) -> Settings:
    return replace(settings, firecrawl_api_key=FIRECRAWL_KEY, **overrides)


def firecrawl_page(markdown: str, title: str | None = None, status: int = 200) -> httpx.Response:
    metadata: dict = {"sourceURL": "https://example.test", "statusCode": status}
    if title is not None:
        metadata["title"] = title
    return httpx.Response(
        200, json={"success": True, "data": {"markdown": markdown, "metadata": metadata}}
    )


@pytest.fixture
def blobs() -> InMemoryBlobStore:
    return InMemoryBlobStore()


@pytest.fixture
def services(
    settings: Settings, store: InMemoryStore, blobs: InMemoryBlobStore
) -> Services:
    return build_services(settings, store, blobs=blobs)


@pytest.fixture
def project_id(services: Services):
    return services.projects.create("Docs").id


def jobs_of_type(store: InMemoryStore, job_type: str) -> list:
    return [j for j in store.jobs.values() if j.type == job_type]


def test_add_text_creates_pending_version_blob_and_job(
    services: Services, store: InMemoryStore, blobs: InMemoryBlobStore, project_id
):
    source = services.sources.add_text(project_id, "  Notes ", "hello world")

    assert source.kind is SourceKind.TEXT
    assert source.title == "Notes"
    assert source.status is SourceStatus.PENDING
    assert source.current_version is None

    versions = services.sources.versions(project_id, source.id)
    assert [v.version for v in versions] == [1]
    version = versions[0]
    assert version.status is VersionStatus.PENDING
    assert version.extracted_text == "hello world"
    assert version.extracted_chars == 11
    assert version.storage_key == f"{project_id}/{source.id}/1.txt"
    assert blobs.get(version.storage_key) == b"hello world"
    assert version.content_hash is not None

    jobs = jobs_of_type(store, INDEX_VERSION_JOB)
    assert len(jobs) == 1
    assert jobs[0].status is JobStatus.PENDING
    assert jobs[0].project_id == project_id
    assert jobs[0].payload == {
        "source_id": str(source.id),
        "version_id": str(version.id),
        "version": 1,
    }


def test_add_text_rejects_empty_text_and_title(services: Services, project_id):
    with pytest.raises(ValidationError):
        services.sources.add_text(project_id, "Notes", "   ")
    with pytest.raises(ValidationError):
        services.sources.add_text(project_id, " ", "text")


def test_add_text_to_unknown_project_is_not_found(services: Services):
    with pytest.raises(NotFound):
        services.sources.add_text(uuid4(), "Notes", "text")


def test_add_text_over_limit_is_payload_too_large(
    store: InMemoryStore, project_id, settings: Settings
):
    small = Settings(
        database_url="",
        email=OWNER_EMAIL,
        password=OWNER_PASSWORD,
        mcp_token=MCP_TOKEN,
        max_upload_bytes=SMALL_LIMIT,
    )
    services = build_services(small, store)
    with pytest.raises(PayloadTooLarge):
        services.sources.add_text(project_id, "Big", "x" * (SMALL_LIMIT + 1))
    assert jobs_of_type(store, INDEX_VERSION_JOB) == []
    assert store.sources == {}


@pytest.mark.parametrize(
    ("filename", "content_type", "data", "expected"),
    [
        ("note.txt", "text/plain", b"plain text", "plain text"),
        ("readme.MD", "text/markdown", b"# Title", "# Title"),
    ],
)
def test_add_file_text_formats(
    services: Services, project_id, filename, content_type, data, expected
):
    source = services.sources.add_file(project_id, filename, data, content_type)
    assert source.kind is SourceKind.FILE
    assert source.title == filename
    assert source.origin_key is None
    version = services.sources.versions(project_id, source.id)[0]
    assert version.extracted_text == expected
    assert version.storage_key.endswith(f"/1.{filename.rsplit('.', 1)[1].lower()}")


def test_add_file_docx_and_pdf_are_extracted(services: Services, project_id):
    docx_source = services.sources.add_file(
        project_id, "doc.docx", make_docx("Inside docx"), None, "Docx"
    )
    assert docx_source.title == "Docx"
    assert (
        services.sources.versions(project_id, docx_source.id)[0].extracted_text
        == "Inside docx"
    )

    pdf_source = services.sources.add_file(
        project_id, "blank.pdf", make_pdf(), "application/pdf"
    )
    assert pdf_source.status is SourceStatus.PENDING
    assert (
        services.sources.versions(project_id, pdf_source.id)[0].extracted_text
        is not None
    )


def test_add_file_unsupported_extension_and_corrupt_pdf(
    services: Services, store: InMemoryStore, project_id
):
    with pytest.raises(UnsupportedFormat):
        services.sources.add_file(
            project_id, "tool.exe", b"MZ", "application/octet-stream"
        )
    with pytest.raises(UnsupportedFormat):
        services.sources.add_file(project_id, "noext", b"data", None)
    with pytest.raises(UnsupportedFormat):
        services.sources.add_file(
            project_id, "broken.pdf", b"not a pdf", "application/pdf"
        )
    assert store.sources == {}
    assert jobs_of_type(store, INDEX_VERSION_JOB) == []


def test_add_file_over_limit(store: InMemoryStore, project_id):
    small = Settings(
        database_url="",
        email=OWNER_EMAIL,
        password=OWNER_PASSWORD,
        mcp_token=MCP_TOKEN,
        max_upload_bytes=SMALL_LIMIT,
    )
    services = build_services(small, store)
    with pytest.raises(PayloadTooLarge):
        services.sources.add_file(
            project_id, "big.txt", b"x" * (SMALL_LIMIT + 1), "text/plain"
        )


def test_add_url_indexes_full_firecrawl_markdown(
    store: InMemoryStore, settings: Settings, blobs: InMemoryBlobStore
):
    seen: list[httpx.Request] = []
    sections = [f"## Section {i}\n\n" + f"paragraph {i} " * 60 for i in range(30)]
    markdown = "# Guide\n\n" + "\n\n".join(sections) + "\n\nTAIL-MARKER-END"

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return firecrawl_page(markdown, title="  Full Guide  ")

    text, vector = InMemoryTextIndex(), InMemoryVectorIndex()
    config = firecrawl_settings(settings)
    services = build_services(
        config,
        store,
        blobs=blobs,
        transport=url_transport(handler),
        text_index=text,
        vector_index=vector,
    )
    project_id = services.projects.create("Links").id

    source = services.sources.add_url(project_id, "  https://example.test/guide  ")

    assert len(seen) == 1
    assert json.loads(seen[0].content)["url"] == "https://example.test/guide"
    assert source.kind is SourceKind.URL
    assert source.title == "Full Guide"
    assert source.origin_key == "https://example.test/guide"
    assert source.content_type == "text/markdown"
    assert source.status is SourceStatus.PENDING
    version = services.sources.versions(project_id, source.id)[0]
    assert version.extracted_text == markdown
    assert version.extracted_chars == len(markdown)
    assert version.storage_key == f"{project_id}/{source.id}/1.md"
    assert blobs.get(version.storage_key) == markdown.encode("utf-8")
    jobs = jobs_of_type(store, INDEX_VERSION_JOB)
    assert len(jobs) == 1

    indexing = IndexingService(memory_indexing_scope(store), text, vector, NullEmbedder(), config)
    indexing.index_version(project_id, source.id, version.id, final_attempt=True)

    chunks = [c for c in store.chunks.values() if c.version_id == version.id]
    assert len(chunks) > 10
    assert set(text.docs) == {c.id for c in chunks}
    assert any("TAIL-MARKER-END" in c.text for c in chunks)
    assert services.sources.get(project_id, source.id).status is SourceStatus.INDEXED


def test_add_url_explicit_title_wins_over_page_title(
    store: InMemoryStore, settings: Settings
):
    services = build_services(
        firecrawl_settings(settings),
        store,
        transport=url_transport(lambda r: firecrawl_page("body", title="Page Title")),
    )
    project_id = services.projects.create("Links").id

    source = services.sources.add_url(project_id, "https://example.test/a", title=" Mine ")

    assert source.title == "Mine"


def test_add_url_without_page_title_keeps_url_title(
    store: InMemoryStore, settings: Settings
):
    services = build_services(
        firecrawl_settings(settings),
        store,
        transport=url_transport(lambda r: firecrawl_page("body", title=None)),
    )
    project_id = services.projects.create("Links").id

    source = services.sources.add_url(project_id, "https://example.test/docs/page")

    assert source.title == "page"


def test_add_url_without_firecrawl_key_is_rejected_without_requests(
    services: Services, store: InMemoryStore, project_id
):
    with pytest.raises(ServiceUnavailable) as excinfo:
        services.sources.add_url(project_id, "https://example.test/page")

    assert str(excinfo.value) == "link ingestion requires FIRECRAWL_API_KEY"
    assert excinfo.value.status_code == 503
    assert excinfo.value.code == "link_ingestion_not_configured"
    assert store.sources == {}
    assert jobs_of_type(store, INDEX_VERSION_JOB) == []


def test_add_url_without_firecrawl_key_sends_nothing(
    store: InMemoryStore, settings: Settings
):
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return firecrawl_page("body")

    services = build_services(settings, store, transport=url_transport(handler))
    project_id = services.projects.create("Links").id

    with pytest.raises(ServiceUnavailable):
        services.sources.add_url(project_id, "https://example.test/page")
    assert calls == []
    assert store.sources == {}


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (httpx.Response(402, json={"success": False}), "firecrawl: credits exhausted"),
        (httpx.Response(429, json={"success": False}), "firecrawl: rate limit exceeded"),
        (
            httpx.Response(401, json={"error": "invalid token"}),
            "firecrawl: request rejected (check FIRECRAWL_API_KEY)",
        ),
        (httpx.Response(502, text="bad gateway"), "url could not be fetched via firecrawl"),
        (
            httpx.Response(200, json={"success": False, "data": {"metadata": {"statusCode": 404}}}),
            "url returned status 404",
        ),
        (httpx.Response(200, json={"success": True, "data": {"markdown": " "}}), "no content extracted from url"),
    ],
)
def test_add_url_firecrawl_error_marks_source_failed(
    store: InMemoryStore, settings: Settings, response: httpx.Response, message: str
):
    services = build_services(
        firecrawl_settings(settings), store, transport=url_transport(lambda r: response)
    )
    project_id = services.projects.create("Links").id

    source = services.sources.add_url(project_id, "https://down.test/doc", title="Down")

    assert source.status is SourceStatus.FAILED
    assert source.error_message == message
    assert FIRECRAWL_KEY not in source.error_message
    assert services.sources.versions(project_id, source.id) == []
    assert jobs_of_type(store, INDEX_VERSION_JOB) == []
    assert services.sources.get(project_id, source.id).title == "Down"


def test_add_url_timeout_marks_failed_without_leaking_key(
    store: InMemoryStore, settings: Settings, caplog: pytest.LogCaptureFixture
):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout(request.headers["authorization"], request=request)

    services = build_services(
        firecrawl_settings(settings), store, transport=url_transport(handler)
    )
    project_id = services.projects.create("Links").id

    with caplog.at_level("DEBUG"):
        source = services.sources.add_url(project_id, "https://slow.test/doc")

    assert source.status is SourceStatus.FAILED
    assert source.error_message == "url could not be fetched via firecrawl"
    assert FIRECRAWL_KEY not in caplog.text
    assert all(FIRECRAWL_KEY not in str(vars(s)) for s in store.sources.values())


def test_add_url_over_size_limit_marks_failed(store: InMemoryStore, settings: Settings):
    services = build_services(
        firecrawl_settings(settings, max_upload_bytes=SMALL_LIMIT),
        store,
        transport=url_transport(lambda r: firecrawl_page("x" * (SMALL_LIMIT + 1))),
    )
    project_id = services.projects.create("Links").id

    source = services.sources.add_url(project_id, "https://big.test/doc")

    assert source.status is SourceStatus.FAILED
    assert source.error_message == "remote content exceeds size limit"
    assert services.sources.versions(project_id, source.id) == []


def test_add_url_rejects_non_http_scheme(
    store: InMemoryStore, settings: Settings
):
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return firecrawl_page("body")

    services = build_services(
        firecrawl_settings(settings), store, transport=url_transport(handler)
    )
    project_id = services.projects.create("Links").id
    with pytest.raises(ValidationError):
        services.sources.add_url(project_id, "ftp://example.test/file")
    assert store.sources == {}
    assert calls == []


def test_add_url_duplicate_with_versions_is_conflict(
    store: InMemoryStore, settings: Settings
):
    services = build_services(
        firecrawl_settings(settings),
        store,
        transport=url_transport(lambda r: firecrawl_page("body")),
    )
    project_id = services.projects.create("Links").id
    other_project = services.projects.create("Other").id
    first = services.sources.add_url(project_id, "https://example.test/a")

    with pytest.raises(Conflict):
        services.sources.add_url(project_id, "https://example.test/a")
    assert len(store.sources) == 1
    assert (
        services.sources.add_url(other_project, "https://example.test/a").id != first.id
    )


def test_add_url_retries_failed_source_on_same_origin(
    store: InMemoryStore, settings: Settings
):
    attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        if attempts["count"] == 1:
            return httpx.Response(429, json={"success": False})
        return firecrawl_page("now up", title="Page Title")

    services = build_services(
        firecrawl_settings(settings), store, transport=url_transport(handler)
    )
    project_id = services.projects.create("Links").id
    failed = services.sources.add_url(project_id, "https://flaky.test/doc")
    assert failed.status is SourceStatus.FAILED
    assert failed.error_message == "firecrawl: rate limit exceeded"

    retried = services.sources.add_url(project_id, "https://flaky.test/doc")

    assert attempts["count"] == 2
    assert retried.id == failed.id
    assert retried.title == "Page Title"
    assert retried.status is SourceStatus.PENDING
    assert retried.error_message is None
    assert retried.content_type == "text/markdown"
    assert [v.version for v in services.sources.versions(project_id, failed.id)] == [1]
    assert len(store.sources) == 1
    assert len(jobs_of_type(store, INDEX_VERSION_JOB)) == 1


def test_settings_repr_hides_firecrawl_key(settings: Settings):
    configured = firecrawl_settings(settings)

    assert FIRECRAWL_KEY not in repr(configured)
    assert "firecrawl_api_key" not in repr(configured)


def test_blob_written_in_rolled_back_transaction_is_removed(
    store: InMemoryStore, settings: Settings, blobs: InMemoryBlobStore
):
    hooks = TransactionHooks()
    services = build_services(settings, store, blobs=blobs, hooks=hooks)
    project_id = services.projects.create("Docs").id
    source = services.sources.add_text(project_id, "Notes", "hello")
    key = services.sources.versions(project_id, source.id)[0].storage_key
    assert blobs.exists(key)

    hooks.rollback()

    assert not blobs.exists(key)


def test_blob_kept_when_transaction_commits(
    store: InMemoryStore, settings: Settings, blobs: InMemoryBlobStore
):
    hooks = TransactionHooks()
    services = build_services(settings, store, blobs=blobs, hooks=hooks)
    project_id = services.projects.create("Docs").id
    source = services.sources.add_text(project_id, "Notes", "hello")
    key = services.sources.versions(project_id, source.id)[0].storage_key

    hooks.commit()
    hooks.rollback()

    assert blobs.exists(key)


def test_delete_keeps_blobs_when_transaction_rolls_back(
    store: InMemoryStore, settings: Settings, blobs: InMemoryBlobStore
):
    hooks = TransactionHooks()
    services = build_services(settings, store, blobs=blobs, hooks=hooks)
    project_id = services.projects.create("Docs").id
    source = services.sources.add_text(project_id, "Notes", "hello")
    key = services.sources.versions(project_id, source.id)[0].storage_key
    hooks.commit()

    services.sources.delete(project_id, source.id)
    assert blobs.exists(key)
    hooks.rollback()
    assert blobs.exists(key)


def test_delete_blob_cleanup_runs_on_commit(
    store: InMemoryStore, settings: Settings, blobs: InMemoryBlobStore
):
    hooks = TransactionHooks()
    services = build_services(settings, store, blobs=blobs, hooks=hooks)
    project_id = services.projects.create("Docs").id
    source = services.sources.add_text(project_id, "Notes", "hello")
    key = services.sources.versions(project_id, source.id)[0].storage_key
    hooks.commit()

    services.sources.delete(project_id, source.id)
    assert blobs.exists(key)
    hooks.commit()
    assert not blobs.exists(key)


def test_transaction_hooks_survive_failing_actions(monkeypatch: pytest.MonkeyPatch):
    import vector_core.services.common as common

    logged: list[str] = []
    monkeypatch.setattr(
        common.logger, "exception", lambda msg, *args: logged.append(msg % args)
    )
    hooks = TransactionHooks()
    calls: list[str] = []

    def boom() -> None:
        raise OSError("disk gone")

    hooks.after_commit(boom)
    hooks.after_commit(lambda: calls.append("second"))
    hooks.after_rollback(lambda: calls.append("rollback"))

    hooks.commit()

    assert calls == ["second"]
    assert logged == ["after-commit action failed"]
    hooks.rollback()
    assert calls == ["second"]


def test_edit_text_creates_next_version_and_rename(
    services: Services, store: InMemoryStore, project_id
):
    source = services.sources.add_text(project_id, "Notes", "v1")

    renamed = services.sources.edit(project_id, source.id, title="Renamed")
    assert renamed.title == "Renamed"
    assert [v.version for v in services.sources.versions(project_id, source.id)] == [1]

    updated = services.sources.edit(project_id, source.id, text="v2")
    versions = services.sources.versions(project_id, source.id)
    assert [v.version for v in versions] == [1, 2]
    assert versions[1].extracted_text == "v2"
    assert versions[1].storage_key == f"{project_id}/{source.id}/2.txt"
    assert updated.status is SourceStatus.PENDING
    jobs = jobs_of_type(store, INDEX_VERSION_JOB)
    assert [j.payload["version"] for j in jobs] == [1, 2]


def test_edit_requires_changes_and_text_only_for_text_kind(
    services: Services, project_id
):
    text_source = services.sources.add_text(project_id, "Notes", "v1")
    with pytest.raises(ValidationError):
        services.sources.edit(project_id, text_source.id)

    file_source = services.sources.add_file(project_id, "a.txt", b"file", "text/plain")
    with pytest.raises(ValidationError):
        services.sources.edit(project_id, file_source.id, text="new")


def test_delete_removes_versions_blobs_and_chunks(
    services: Services, store: InMemoryStore, blobs: InMemoryBlobStore, project_id
):
    source = services.sources.add_text(project_id, "Notes", "v1")
    other = services.sources.add_text(project_id, "Other", "keep")
    version = services.sources.versions(project_id, source.id)[0]
    chunk = Chunk(
        id=uuid4(),
        project_id=project_id,
        source_id=source.id,
        version_id=version.id,
        ordinal=0,
        text="v1",
    )
    store.chunks[chunk.id] = chunk

    services.sources.delete(project_id, source.id)

    assert source.id not in store.sources
    assert all(v.source_id != source.id for v in store.versions.values())
    assert all(c.source_id != source.id for c in store.chunks.values())
    assert not blobs.exists(version.storage_key)
    assert blobs.exists(services.sources.versions(project_id, other.id)[0].storage_key)
    with pytest.raises(NotFound):
        services.sources.get(project_id, source.id)


def test_sources_are_isolated_per_project(services: Services):
    project_a = services.projects.create("A").id
    project_b = services.projects.create("B").id
    source = services.sources.add_text(project_a, "A doc", "a")

    assert [s.id for s in services.sources.list_for_project(project_a)] == [source.id]
    assert services.sources.list_for_project(project_b) == []
    with pytest.raises(NotFound):
        services.sources.get(project_b, source.id)
    with pytest.raises(NotFound):
        services.sources.delete(project_b, source.id)
    with pytest.raises(NotFound):
        services.sources.versions(project_b, source.id)


def test_reindex_enqueues_job_for_current_or_latest_version(
    services: Services, store: InMemoryStore, project_id
):
    source = services.sources.add_text(project_id, "Notes", "v1")
    version = services.sources.versions(project_id, source.id)[0]

    job = services.sources.reindex(project_id, source.id)

    assert job.type == INDEX_VERSION_JOB
    assert job.payload["version_id"] == str(version.id)
    assert services.sources.get(project_id, source.id).status is SourceStatus.PENDING
    assert len(jobs_of_type(store, INDEX_VERSION_JOB)) == 2


def test_reindex_without_versions_is_validation_error(
    store: InMemoryStore, settings: Settings
):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    services = build_services(
        firecrawl_settings(settings), store, transport=url_transport(handler)
    )
    project_id = services.projects.create("Links").id
    failed = services.sources.add_url(project_id, "https://down.test/x")
    with pytest.raises(ValidationError):
        services.sources.reindex(project_id, failed.id)


# --- HTTP layer -------------------------------------------------------------


def test_http_source_lifecycle(
    client: TestClient, owner_headers: dict[str, str], store: InMemoryStore
):
    project_id = client.post(
        "/api/projects", json={"name": "Docs"}, headers=owner_headers
    ).json()["id"]
    base = f"/api/projects/{project_id}/sources"

    created = client.post(
        f"{base}/text", json={"title": "Notes", "text": "hello"}, headers=owner_headers
    )
    assert created.status_code == 201
    body = created.json()
    assert body["kind"] == "text"
    assert body["status"] == "pending"
    assert body["current_version"] is None
    source_id = body["id"]

    listed = client.get(base, headers=owner_headers)
    assert listed.status_code == 200
    assert [s["id"] for s in listed.json()] == [source_id]

    assert client.get(f"{base}/{source_id}", headers=owner_headers).status_code == 200

    patched = client.patch(
        f"{base}/{source_id}",
        json={"title": "Renamed", "text": "v2"},
        headers=owner_headers,
    )
    assert patched.status_code == 200
    assert patched.json()["title"] == "Renamed"

    versions = client.get(f"{base}/{source_id}/versions", headers=owner_headers)
    assert versions.status_code == 200
    assert [v["version"] for v in versions.json()] == [1, 2]
    assert all(v["status"] == "pending" for v in versions.json())
    assert "extracted_text" not in versions.json()[0]

    reindexed = client.post(f"{base}/{source_id}/reindex", headers=owner_headers)
    assert reindexed.status_code == 202
    assert reindexed.json()["type"] == INDEX_VERSION_JOB
    assert len(jobs_of_type(store, INDEX_VERSION_JOB)) == 3

    assert (
        client.delete(f"{base}/{source_id}", headers=owner_headers).status_code == 204
    )
    assert client.get(f"{base}/{source_id}", headers=owner_headers).status_code == 404
    assert (
        client.delete(f"{base}/{source_id}", headers=owner_headers).status_code == 404
    )


def test_http_file_upload_and_errors(client: TestClient, owner_headers: dict[str, str]):
    project_id = client.post(
        "/api/projects", json={"name": "Docs"}, headers=owner_headers
    ).json()["id"]
    base = f"/api/projects/{project_id}/sources"

    ok = client.post(
        f"{base}/file",
        files={
            "file": ("doc.docx", make_docx("From docx"), "application/octet-stream")
        },
        data={"title": "Docx"},
        headers=owner_headers,
    )
    assert ok.status_code == 201
    assert ok.json()["kind"] == "file"
    assert ok.json()["title"] == "Docx"

    bad = client.post(
        f"{base}/file",
        files={"file": ("tool.exe", b"MZ", "application/octet-stream")},
        headers=owner_headers,
    )
    assert bad.status_code == 422
    assert bad.json()["detail"]["code"] == "unsupported_format"


def test_http_upload_over_limit_is_413(
    make_client, store: InMemoryStore, owner_headers: dict[str, str]
):
    small = Settings(
        database_url="",
        email=OWNER_EMAIL,
        password=OWNER_PASSWORD,
        mcp_token=MCP_TOKEN,
        max_upload_bytes=SMALL_LIMIT,
    )
    client = make_client(small)
    project_id = client.post(
        "/api/projects", json={"name": "Docs"}, headers=owner_headers
    ).json()["id"]
    base = f"/api/projects/{project_id}/sources"

    too_big = client.post(
        f"{base}/file",
        files={"file": ("big.txt", b"x" * (SMALL_LIMIT + 1), "text/plain")},
        headers=owner_headers,
    )
    assert too_big.status_code == 413
    assert too_big.json()["detail"]["code"] == "payload_too_large"

    big_text = client.post(
        f"{base}/text",
        json={"title": "T", "text": "x" * (SMALL_LIMIT + 1)},
        headers=owner_headers,
    )
    assert big_text.status_code == 413
    assert store.sources == {}


def _owner_client(services: Services) -> tuple[TestClient, dict[str, str], str]:
    from app.deps import get_services
    from app.main import app

    app.dependency_overrides[get_services] = lambda: services
    client = TestClient(app)
    login = client.post(
        "/api/auth/login", json={"email": OWNER_EMAIL, "password": OWNER_PASSWORD}
    )
    headers = {"Authorization": f"Bearer {login.json()['token']}"}
    project_id = client.post(
        "/api/projects", json={"name": "Links"}, headers=headers
    ).json()["id"]
    return client, headers, project_id


def test_http_url_source(store: InMemoryStore, settings: Settings):
    from app.main import app

    services = build_services(
        firecrawl_settings(settings),
        store,
        transport=url_transport(lambda r: firecrawl_page("# A\n\nbody", title="Doc A")),
    )
    client, headers, project_id = _owner_client(services)

    response = client.post(
        f"/api/projects/{project_id}/sources/url",
        json={"url": "https://example.test/a.txt"},
        headers=headers,
    )
    assert response.status_code == 201
    assert response.json()["kind"] == "url"
    assert response.json()["title"] == "Doc A"

    invalid = client.post(
        f"/api/projects/{project_id}/sources/url",
        json={"url": "ftp://x"},
        headers=headers,
    )
    assert invalid.status_code == 422

    duplicate = client.post(
        f"/api/projects/{project_id}/sources/url",
        json={"url": "https://example.test/a.txt"},
        headers=headers,
    )
    assert duplicate.status_code == 409
    app.dependency_overrides.clear()


def test_http_url_source_without_firecrawl_key_is_503(
    store: InMemoryStore, settings: Settings
):
    from app.main import app

    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return firecrawl_page("body")

    services = build_services(settings, store, transport=url_transport(handler))
    client, headers, project_id = _owner_client(services)

    response = client.post(
        f"/api/projects/{project_id}/sources/url",
        json={"url": "https://example.test/a"},
        headers=headers,
    )

    assert response.status_code == 503
    assert response.json() == {
        "detail": {
            "code": "link_ingestion_not_configured",
            "message": "link ingestion requires FIRECRAWL_API_KEY",
        }
    }
    assert calls == []
    assert client.get(
        f"/api/projects/{project_id}/sources", headers=headers
    ).json() == []
    app.dependency_overrides.clear()


def test_http_failed_url_can_be_retried(store: InMemoryStore, settings: Settings):
    from app.main import app

    state = {"up": False}

    def handler(request: httpx.Request) -> httpx.Response:
        if not state["up"]:
            raise httpx.ConnectError("down", request=request)
        return firecrawl_page("back")

    services = build_services(
        firecrawl_settings(settings), store, transport=url_transport(handler)
    )
    client, headers, project_id = _owner_client(services)
    base = f"/api/projects/{project_id}/sources"

    failed = client.post(
        f"{base}/url", json={"url": "https://flaky.test/x"}, headers=headers
    )
    assert failed.status_code == 201
    assert failed.json()["status"] == "failed"
    assert failed.json()["error_message"] == "url could not be fetched via firecrawl"
    assert (
        client.post(
            f"{base}/{failed.json()['id']}/reindex", headers=headers
        ).status_code
        == 422
    )

    state["up"] = True
    retried = client.post(
        f"{base}/url", json={"url": "https://flaky.test/x"}, headers=headers
    )
    assert retried.status_code == 201
    assert retried.json()["id"] == failed.json()["id"]
    assert retried.json()["status"] == "pending"
    assert len(client.get(base, headers=headers).json()) == 1
    app.dependency_overrides.clear()


def test_http_sources_require_owner_session(
    client: TestClient, owner_headers: dict[str, str]
):
    project_id = client.post(
        "/api/projects", json={"name": "Docs"}, headers=owner_headers
    ).json()["id"]
    base = f"/api/projects/{project_id}/sources"
    assert client.get(base).status_code == 401
    assert (
        client.get(base, headers={"Authorization": f"Bearer {MCP_TOKEN}"}).status_code
        == 401
    )
    assert (
        client.post(f"{base}/text", json={"title": "t", "text": "x"}).status_code == 401
    )
