import dataclasses
import hashlib
from uuid import uuid4

import pytest

from app.deps import Services
from tests.fakes import InMemoryBlobStore, InMemoryStore, build_services
from vector_core.config import Settings
from vector_core.domain import (
    Conflict,
    NotFound,
    PayloadTooLarge,
    SourceKind,
    SyncFile,
    SyncFileStatus,
    UnsupportedFormat,
    ValidationError,
)
from vector_core.extract import SUPPORTED_EXTENSIONS
from vector_core.services import REINDEX_PROJECT_JOB
from vector_core.services.sources import sync_origin_key

ROOT = "/data/notes"
OTHER_ROOT = "/data/other"


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def manifest_entry(rel: str, data: bytes) -> SyncFile:
    return SyncFile(rel, digest(data), len(data))


@pytest.fixture
def blobs() -> InMemoryBlobStore:
    return InMemoryBlobStore()


@pytest.fixture
def services(settings: Settings, store: InMemoryStore, blobs: InMemoryBlobStore) -> Services:
    return build_services(settings, store, blobs=blobs)


@pytest.fixture
def project_id(services: Services):
    return services.projects.create("Docs").id


def test_empty_root_uploads_everything(services: Services, project_id):
    plan = services.sources.sync_plan(
        project_id,
        ROOT,
        [manifest_entry("a.md", b"alpha"), manifest_entry("sub/b.txt", b"beta")],
    )

    assert plan.to_upload == ["a.md", "sub/b.txt"]
    assert plan.unchanged == []
    assert plan.to_prune == []


def test_same_hash_is_unchanged_and_changed_hash_is_uploaded(services: Services, project_id):
    services.sources.sync_file(project_id, ROOT, "a.md", b"alpha")

    same = services.sources.sync_plan(project_id, ROOT, [manifest_entry("a.md", b"alpha")])
    changed = services.sources.sync_plan(
        project_id, ROOT, [manifest_entry("a.md", b"alpha 2")]
    )

    assert same.unchanged == ["a.md"]
    assert same.to_upload == []
    assert changed.to_upload == ["a.md"]
    assert changed.unchanged == []


def test_missing_file_is_pruned_and_other_roots_or_manual_sources_are_kept(
    services: Services, project_id
):
    keep, _ = services.sources.sync_file(project_id, ROOT, "keep.md", b"keep")
    gone, _ = services.sources.sync_file(project_id, ROOT, "gone.md", b"gone")
    other, _ = services.sources.sync_file(project_id, OTHER_ROOT, "gone.md", b"gone")
    manual = services.sources.add_text(project_id, "Manual", "manual note")

    plan = services.sources.sync_plan(
        project_id, ROOT, [manifest_entry("keep.md", b"keep")]
    )

    assert plan.to_prune == [gone.id]
    assert other.id not in plan.to_prune
    assert manual.id not in plan.to_prune
    assert keep.id not in plan.to_prune


def test_same_rel_in_two_roots_creates_two_sources(services: Services, project_id):
    first, first_status = services.sources.sync_file(project_id, ROOT, "a.md", b"x")
    second, second_status = services.sources.sync_file(project_id, OTHER_ROOT, "a.md", b"x")

    assert first.id != second.id
    assert first_status is SyncFileStatus.CREATED
    assert second_status is SyncFileStatus.CREATED
    assert first.kind is SourceKind.FILE
    assert first.origin_key == sync_origin_key(ROOT, "a.md")
    assert first.sync_root == ROOT


def test_nested_roots_with_same_joined_path_stay_distinct(services: Services, project_id):
    outer, outer_status = services.sources.sync_file(project_id, "/docs", "sub/x.md", b"outer")
    inner, inner_status = services.sources.sync_file(project_id, "/docs/sub", "x.md", b"inner")

    assert outer.id != inner.id
    assert outer_status is SyncFileStatus.CREATED
    assert inner_status is SyncFileStatus.CREATED

    outer_plan = services.sources.sync_plan(
        project_id, "/docs", [manifest_entry("sub/x.md", b"outer")]
    )
    inner_plan = services.sources.sync_plan(
        project_id, "/docs/sub", [manifest_entry("x.md", b"inner")]
    )
    assert outer_plan.unchanged == ["sub/x.md"]
    assert inner_plan.unchanged == ["x.md"]
    assert outer_plan.to_prune == []
    assert inner_plan.to_prune == []

    _, status = services.sources.sync_file(project_id, "/docs", "sub/x.md", b"outer")
    assert status is SyncFileStatus.UNCHANGED
    _, status = services.sources.sync_file(project_id, "/docs/sub", "x.md", b"inner")
    assert status is SyncFileStatus.UNCHANGED


def test_sync_file_rejects_origin_key_owned_by_other_sync_root(
    services: Services, store: InMemoryStore, project_id
):
    source, _ = services.sources.sync_file(project_id, ROOT, "a.md", b"x")
    store.sources[source.id] = dataclasses.replace(source, sync_root=OTHER_ROOT)

    with pytest.raises(Conflict):
        services.sources.sync_file(project_id, ROOT, "a.md", b"x")


def test_sync_file_same_content_is_unchanged_other_content_adds_version(
    services: Services, project_id
):
    source, status = services.sources.sync_file(project_id, ROOT, "a.md", b"v1")
    assert status is SyncFileStatus.CREATED

    again, status = services.sources.sync_file(project_id, ROOT, "a.md", b"v1")
    assert status is SyncFileStatus.UNCHANGED
    assert again.id == source.id
    assert [v.version for v in services.sources.versions(project_id, source.id)] == [1]

    updated, status = services.sources.sync_file(project_id, ROOT, "a.md", b"v2")
    assert status is SyncFileStatus.UPDATED
    assert updated.id == source.id
    assert [v.version for v in services.sources.versions(project_id, source.id)] == [1, 2]


def test_delete_synced_rejects_foreign_root(services: Services, project_id):
    source, _ = services.sources.sync_file(project_id, ROOT, "a.md", b"x")

    with pytest.raises(NotFound):
        services.sources.delete_synced(project_id, OTHER_ROOT, source.id)

    services.sources.delete_synced(project_id, ROOT, source.id)
    with pytest.raises(NotFound):
        services.sources.get(project_id, source.id)


@pytest.mark.parametrize("rel", ["../secret.md", "/abs/a.md", "a/../b.md", "a//b.md", "./a.md", "a\\b.md", ""])
def test_unsafe_relative_paths_are_rejected(services: Services, project_id, rel: str):
    with pytest.raises(ValidationError):
        services.sources.sync_plan(project_id, ROOT, [SyncFile(rel, digest(b"x"), 1)])
    with pytest.raises(ValidationError):
        services.sources.sync_file(project_id, ROOT, rel, b"x")


def test_unsupported_extension_is_rejected(services: Services, project_id):
    with pytest.raises(UnsupportedFormat):
        services.sources.sync_file(project_id, ROOT, "tool.exe", b"MZ")


def test_sync_file_respects_size_limit(settings: Settings, store: InMemoryStore, project_id):
    small = build_services(dataclasses.replace(settings, max_upload_bytes=4), store)

    with pytest.raises(PayloadTooLarge):
        small.sources.sync_file(project_id, ROOT, "a.md", b"12345")


def test_sync_plan_rejects_duplicates_bad_hash_and_negative_size(
    services: Services, project_id
):
    with pytest.raises(ValidationError):
        services.sources.sync_plan(
            project_id,
            ROOT,
            [manifest_entry("a.md", b"x"), manifest_entry("a.md", b"x")],
        )
    with pytest.raises(ValidationError):
        services.sources.sync_plan(project_id, ROOT, [SyncFile("a.md", "ABC", 1)])
    with pytest.raises(ValidationError):
        services.sources.sync_plan(project_id, ROOT, [SyncFile("a.md", digest(b"x"), -1)])


def test_sync_plan_for_unknown_project_is_not_found(services: Services):
    with pytest.raises(NotFound):
        services.sources.sync_plan(uuid4(), ROOT, [])


def test_reindex_project_enqueues_job(services: Services, store: InMemoryStore, project_id):
    job = services.sources.reindex_project(project_id)

    assert job.type == REINDEX_PROJECT_JOB
    assert job.project_id == project_id
    assert job.payload == {}


def test_cli_supported_extensions_match_core():
    from vector_cli.main import SUPPORTED_EXTENSIONS as CLI_EXTENSIONS

    assert CLI_EXTENSIONS == SUPPORTED_EXTENSIONS
