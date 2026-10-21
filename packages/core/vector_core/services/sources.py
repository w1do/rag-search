import hashlib
import logging
import re
from dataclasses import replace
from pathlib import PurePosixPath
from urllib.parse import quote, urlsplit
from uuid import UUID, uuid4

from vector_core.config import Settings
from vector_core.domain import (
    Conflict,
    DomainError,
    Job,
    NotFound,
    PayloadTooLarge,
    ServiceUnavailable,
    Source,
    SourceKind,
    SourceStatus,
    SourceVersion,
    SyncFile,
    SyncFileStatus,
    SyncPlan,
    UnsupportedFormat,
    ValidationError,
    VersionStatus,
    utcnow,
)
from vector_core.extract import SUPPORTED_EXTENSIONS, check_link_url
from vector_core.ports import (
    BlobStore,
    ContentExtractor,
    IndexBackendError,
    LinkFetcher,
    TextIndex,
    VectorIndex,
)
from vector_core.repositories import (
    ChunkRepository,
    ProjectRepository,
    SourceRepository,
    VersionRepository,
)
from vector_core.services.common import TransactionHooks, clean_name
from vector_core.services.indexing import REINDEX_PROJECT_JOB
from vector_core.services.jobs import JobQueueService

INDEX_VERSION_JOB = "index_version"
PURGE_SOURCE_JOB = "purge_source"
TEXT_EXTENSION = "txt"
LINK_EXTENSION = "md"
LINK_CONTENT_TYPE = "text/markdown"
LINK_INGESTION_NOT_CONFIGURED = "link ingestion requires FIRECRAWL_API_KEY"
logger = logging.getLogger(__name__)


def content_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_extension(filename: str | None) -> str:
    return PurePosixPath(filename).suffix.lower().lstrip(".") if filename else ""


def _media_type(content_type: str | None) -> str:
    return (content_type or "").split(";", 1)[0].strip().lower()


def _url_title(url: str) -> str:
    parts = urlsplit(url)
    name = PurePosixPath(parts.path).name
    return name or parts.netloc or url


SYNC_SCHEME = "sync"
SYNC_ROOT_MAX_LENGTH = 1024
CONTENT_HASH_PATTERN = re.compile(r"[0-9a-f]{64}")


def sync_origin_key(sync_root: str, rel: str) -> str:
    return f"{SYNC_SCHEME}://{quote(sync_root, safe='')}/{rel}"


def _check_sync_root(sync_root: str) -> None:
    if not sync_root.strip() or len(sync_root) > SYNC_ROOT_MAX_LENGTH:
        raise ValidationError(f"sync_root must be 1-{SYNC_ROOT_MAX_LENGTH} characters")


def _check_rel(rel: str) -> None:
    parts = rel.split("/")
    if not rel or rel.startswith("/") or "\\" in rel or any(p in {"", ".", ".."} for p in parts):
        raise ValidationError("origin_key must be a relative posix path")
    extension = file_extension(rel)
    if extension not in SUPPORTED_EXTENSIONS:
        raise UnsupportedFormat(f"unsupported file extension: {extension or 'none'}")


def _check_hash(value: str) -> None:
    if CONTENT_HASH_PATTERN.fullmatch(value) is None:
        raise ValidationError("content_hash must be 64 lowercase hex characters")


class SourceService:
    def __init__(
        self,
        settings: Settings,
        sources: SourceRepository,
        versions: VersionRepository,
        chunks: ChunkRepository,
        projects: ProjectRepository,
        blobs: BlobStore,
        extractor: ContentExtractor,
        jobs: JobQueueService,
        link_fetcher: LinkFetcher | None = None,
        *,
        hooks: TransactionHooks,
        text_index: TextIndex,
        vector_index: VectorIndex,
    ) -> None:
        self._settings = settings
        self._sources = sources
        self._versions = versions
        self._chunks = chunks
        self._projects = projects
        self._blobs = blobs
        self._extractor = extractor
        self._jobs = jobs
        self._link_fetcher = link_fetcher
        self._hooks = hooks
        self._text_index = text_index
        self._vector_index = vector_index

    @property
    def max_upload_bytes(self) -> int:
        return self._settings.max_upload_bytes

    def add_text(self, project_id: UUID, title: str, text: str) -> Source:
        self._require_project(project_id)
        data = self._text_payload(text)
        source = self._new_source(
            project_id, SourceKind.TEXT, clean_name(title, "title"), "text/plain"
        )
        self._sources.add(source)
        self._store_version(source, data, TEXT_EXTENSION, text)
        return self._refresh(source.id)

    def add_file(
        self,
        project_id: UUID,
        filename: str,
        data: bytes,
        content_type: str | None = None,
        title: str | None = None,
    ) -> Source:
        self._require_project(project_id)
        extension = file_extension(filename)
        if extension not in SUPPORTED_EXTENSIONS:
            raise UnsupportedFormat(
                f"unsupported file extension: {extension or 'none'}"
            )
        self._check_size(len(data))
        text = self._extractor.extract(data, content_type, filename)
        source = self._new_source(
            project_id,
            SourceKind.FILE,
            clean_name(title or filename, "title"),
            content_type,
        )
        self._sources.add(source)
        self._store_version(source, data, extension, text)
        return self._refresh(source.id)

    def add_url(self, project_id: UUID, url: str, title: str | None = None) -> Source:
        """Add a link; re-posting a link whose earlier fetch failed retries it on the same source."""
        self._require_project(project_id)
        cleaned_url = url.strip()
        check_link_url(cleaned_url)
        if self._link_fetcher is None:
            raise ServiceUnavailable(
                LINK_INGESTION_NOT_CONFIGURED, code="link_ingestion_not_configured"
            )
        existing = self._sources.get_by_origin(project_id, cleaned_url)
        if existing is None:
            source = self._new_source(
                project_id,
                SourceKind.URL,
                clean_name(title or _url_title(cleaned_url), "title"),
                None,
                origin_key=cleaned_url,
            )
            self._sources.add(source)
        else:
            source = self._lock(project_id, existing.id)
            if self._versions.list_by_source(source.id):
                raise Conflict("url already added to this project")
            if title is not None:
                source = replace(source, title=clean_name(title, "title"))
        self._fetch_url_version(
            self._link_fetcher, source, cleaned_url, explicit_title=title is not None
        )
        return self._refresh(source.id)

    def _fetch_url_version(
        self, fetcher: LinkFetcher, source: Source, url: str, *, explicit_title: bool
    ) -> Job | None:
        try:
            fetched = fetcher.fetch(url)
        except DomainError as exc:
            self._mark_failed(source, str(exc))
            return None
        title = source.title
        if not explicit_title and fetched.title:
            title = clean_name(fetched.title, "title")
        source = replace(source, title=title, content_type=LINK_CONTENT_TYPE)
        self._sources.update(source)
        data = fetched.markdown.encode("utf-8")
        return self._store_version(source, data, LINK_EXTENSION, fetched.markdown)

    def list_for_project(self, project_id: UUID) -> list[Source]:
        self._require_project(project_id)
        return self._sources.list_by_project(project_id)

    def get(self, project_id: UUID, source_id: UUID) -> Source:
        source = self._sources.get(source_id)
        if source is None or source.project_id != project_id:
            raise NotFound("source not found")
        return source

    def versions(self, project_id: UUID, source_id: UUID) -> list[SourceVersion]:
        self.get(project_id, source_id)
        return self._versions.list_by_source(source_id)

    def edit(
        self,
        project_id: UUID,
        source_id: UUID,
        title: str | None = None,
        text: str | None = None,
    ) -> Source:
        if title is None and text is None:
            raise ValidationError("nothing to update")
        source = self._lock(project_id, source_id)
        if title is not None:
            source = replace(
                source, title=clean_name(title, "title"), updated_at=utcnow()
            )
            self._sources.update(source)
        if text is not None:
            if source.kind is not SourceKind.TEXT:
                raise ValidationError("only text sources can be edited inline")
            data = self._text_payload(text)
            self._store_version(source, data, TEXT_EXTENSION, text)
        return self._refresh(source.id)

    def delete(self, project_id: UUID, source_id: UUID) -> None:
        source = self._lock(project_id, source_id)
        self._purge_indexes(source)
        self._chunks.delete_by_source(source_id)
        self._sources.delete(source_id)
        prefix = f"{source.project_id}/{source.id}"
        self._hooks.after_commit(lambda: self._blobs.delete_prefix(prefix))

    def reindex(self, project_id: UUID, source_id: UUID) -> Job:
        source = self._lock(project_id, source_id)
        versions = self._versions.list_by_source(source_id)
        if not versions:
            raise ValidationError(
                "source has no versions to index; add it again to retry"
            )
        target = None
        if source.current_version is not None:
            target = self._versions.get_by_number(source_id, source.current_version)
        if target is None:
            target = versions[-1]
        self._sources.update(
            replace(
                source,
                status=SourceStatus.PENDING,
                error_message=None,
                updated_at=utcnow(),
            )
        )
        return self._enqueue_index(source, target)

    def sync_plan(self, project_id: UUID, sync_root: str, files: list[SyncFile]) -> SyncPlan:
        self._require_project(project_id)
        _check_sync_root(sync_root)
        rels: set[str] = set()
        for item in files:
            _check_rel(item.origin_key)
            _check_hash(item.content_hash)
            if item.size < 0:
                raise ValidationError("size must not be negative")
            if item.origin_key in rels:
                raise ValidationError(f"duplicate origin_key: {item.origin_key}")
            rels.add(item.origin_key)
        existing = {
            s.origin_key: s for s in self._sources.list_by_sync_root(project_id, sync_root)
        }
        to_upload: list[str] = []
        unchanged: list[str] = []
        for item in files:
            source = existing.get(sync_origin_key(sync_root, item.origin_key))
            latest = self._latest_version(source.id) if source is not None else None
            if latest is not None and latest.content_hash == item.content_hash:
                unchanged.append(item.origin_key)
            else:
                to_upload.append(item.origin_key)
        wanted = {sync_origin_key(sync_root, rel) for rel in rels}
        to_prune = sorted(s.id for key, s in existing.items() if key not in wanted)
        return SyncPlan(sorted(to_upload), sorted(unchanged), to_prune)

    def sync_file(
        self,
        project_id: UUID,
        sync_root: str,
        rel: str,
        data: bytes,
        content_type: str | None = None,
    ) -> tuple[Source, SyncFileStatus]:
        self._require_project(project_id)
        _check_sync_root(sync_root)
        _check_rel(rel)
        self._check_size(len(data))
        key = sync_origin_key(sync_root, rel)
        extension = file_extension(rel)
        existing = self._sources.get_by_origin(project_id, key)
        if existing is not None and existing.sync_root != sync_root:
            raise Conflict("origin_key is already synced from another sync_root")
        if existing is None:
            text = self._extractor.extract(data, content_type, rel)
            source = self._new_source(
                project_id,
                SourceKind.FILE,
                clean_name(rel, "title"),
                content_type,
                origin_key=key,
                sync_root=sync_root,
            )
            self._sources.add(source)
            self._store_version(source, data, extension, text)
            return self._refresh(source.id), SyncFileStatus.CREATED
        source = self._lock(project_id, existing.id)
        latest = self._latest_version(source.id)
        if latest is not None and latest.content_hash == content_hash(data):
            return source, SyncFileStatus.UNCHANGED
        text = self._extractor.extract(data, content_type, rel)
        self._store_version(source, data, extension, text)
        return self._refresh(source.id), SyncFileStatus.UPDATED

    def delete_synced(self, project_id: UUID, sync_root: str, source_id: UUID) -> None:
        source = self.get(project_id, source_id)
        if source.sync_root != sync_root:
            raise NotFound("source not found")
        self.delete(project_id, source_id)

    def reindex_project(self, project_id: UUID) -> Job:
        self._require_project(project_id)
        return self._jobs.enqueue(project_id, REINDEX_PROJECT_JOB, {})

    def _latest_version(self, source_id: UUID) -> SourceVersion | None:
        return max(
            self._versions.list_by_source(source_id),
            key=lambda v: v.version,
            default=None,
        )

    def _store_version(
        self, source: Source, data: bytes, extension: str, text: str
    ) -> Job:
        number = self._versions.next_version(source.id)
        version_id = uuid4()
        storage_key = f"{source.project_id}/{source.id}/{number}.{extension}"
        self._blobs.put(storage_key, data)
        self._hooks.after_rollback(lambda: self._blobs.delete(storage_key))
        version = SourceVersion(
            id=version_id,
            source_id=source.id,
            version=number,
            status=VersionStatus.PENDING,
            created_at=utcnow(),
            storage_key=storage_key,
            content_hash=content_hash(data),
            extracted_text=text,
            extracted_chars=len(text),
        )
        self._versions.add(version)
        self._sources.update(
            replace(
                source,
                status=SourceStatus.PENDING,
                error_message=None,
                updated_at=utcnow(),
            )
        )
        return self._enqueue_index(source, version)

    def _enqueue_index(self, source: Source, version: SourceVersion) -> Job:
        return self._jobs.enqueue(
            source.project_id,
            INDEX_VERSION_JOB,
            {
                "source_id": str(source.id),
                "version_id": str(version.id),
                "version": version.version,
            },
        )

    def _purge_indexes(self, source: Source) -> None:
        failed = False
        for name, index in (("text", self._text_index), ("vector", self._vector_index)):
            try:
                index.delete_by_source(source.id)
            except IndexBackendError as exc:
                logger.warning("%s index purge for source %s failed: %s", name, source.id, exc)
                failed = True
        if failed:
            self._jobs.enqueue(
                source.project_id, PURGE_SOURCE_JOB, {"source_id": str(source.id)}
            )

    def _mark_failed(self, source: Source, message: str) -> None:
        self._sources.update(
            replace(
                source,
                status=SourceStatus.FAILED,
                error_message=message,
                updated_at=utcnow(),
            )
        )

    def _text_payload(self, text: str) -> bytes:
        if not text.strip():
            raise ValidationError("text must not be empty")
        data = text.encode("utf-8")
        self._check_size(len(data))
        return data

    def _check_size(self, size: int) -> None:
        if size > self._settings.max_upload_bytes:
            raise PayloadTooLarge(
                f"payload exceeds {self._settings.max_upload_bytes} bytes"
            )

    def _new_source(
        self,
        project_id: UUID,
        kind: SourceKind,
        title: str,
        content_type: str | None,
        origin_key: str | None = None,
        sync_root: str | None = None,
    ) -> Source:
        now = utcnow()
        return Source(
            id=uuid4(),
            project_id=project_id,
            kind=kind,
            title=title,
            status=SourceStatus.PENDING,
            created_at=now,
            updated_at=now,
            origin_key=origin_key,
            sync_root=sync_root,
            content_type=_media_type(content_type) or None,
        )

    def _require_project(self, project_id: UUID) -> None:
        if self._projects.get(project_id) is None:
            raise NotFound("project not found")

    def _refresh(self, source_id: UUID) -> Source:
        source = self._sources.get(source_id)
        if source is None:
            raise NotFound("source not found")
        return source

    def _lock(self, project_id: UUID, source_id: UUID) -> Source:
        """Load the source under a row lock so concurrent writers serialise on version numbers."""
        source = self._sources.get_for_update(source_id)
        if source is None or source.project_id != project_id:
            raise NotFound("source not found")
        return source
