import logging
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass, replace
from uuid import UUID, uuid5

from vector_core.chunking import chunk_text
from vector_core.config import Settings
from vector_core.domain import Chunk, Source, SourceStatus, SourceVersion, VersionStatus, utcnow
from vector_core.ports import Embedder, IndexBackendError, TextIndex, VectorIndex
from vector_core.repositories import ChunkRepository, SourceRepository, VersionRepository

REINDEX_PROJECT_JOB = "reindex_project"
MAX_ERROR_LENGTH = 2000

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class IndexingRepos:
    sources: SourceRepository
    versions: VersionRepository
    chunks: ChunkRepository


IndexingScope = Callable[[], AbstractContextManager[IndexingRepos]]


def chunk_id(version_id: UUID, ordinal: int) -> UUID:
    return uuid5(version_id, str(ordinal))


def _error_text(exc: BaseException) -> str:
    return (str(exc) or type(exc).__name__)[:MAX_ERROR_LENGTH]


def _superseded(source: Source, version: SourceVersion) -> bool:
    return source.current_version is not None and source.current_version >= version.version


class IndexingService:
    def __init__(
        self,
        scope: IndexingScope,
        text_index: TextIndex,
        vector_index: VectorIndex,
        embedder: Embedder,
        settings: Settings,
    ) -> None:
        self._scope = scope
        self._text = text_index
        self._vector = vector_index
        self._embedder = embedder
        self._settings = settings

    def index_version(
        self,
        project_id: UUID,
        source_id: UUID,
        version_id: UUID,
        *,
        final_attempt: bool = True,
    ) -> None:
        with self._scope() as repos:
            claimed = self._claim(repos, project_id, source_id, version_id)
            if claimed is None:
                return
            source, version = claimed
            chunks = self._store_chunks(repos, source, version)
        try:
            vector_indexed = self._index_external(chunks)
            with self._scope() as repos:
                discarded = self._publish(repos, source_id, version_id, vector_indexed)
        except Exception as exc:
            self._record_failure(source_id, version_id, _error_text(exc), final_attempt)
            raise
        for dropped in discarded:
            self._best_effort(lambda dropped=dropped: self._discard(dropped))

    def reindex_project(self, project_id: UUID) -> None:
        self._text.delete_by_project(project_id)
        self._vector.delete_by_project(project_id)
        with self._scope() as repos:
            chunks = repos.chunks.list_published_by_project(project_id)
        groups: dict[UUID, list[Chunk]] = {}
        for chunk in chunks:
            groups.setdefault(chunk.version_id, []).append(chunk)
        self._text.ensure_collection()
        vector_indexed: dict[UUID, bool] = {}
        for version_id, group in groups.items():
            vector_indexed[version_id] = self._index_external(group)
        with self._scope() as repos:
            for version_id, indexed in vector_indexed.items():
                version = repos.versions.get(version_id)
                if version is not None and version.vector_indexed != indexed:
                    repos.versions.update(replace(version, vector_indexed=indexed))

    def purge_source(self, source_id: UUID) -> None:
        self._text.delete_by_source(source_id)
        self._vector.delete_by_source(source_id)

    def _claim(
        self,
        repos: IndexingRepos,
        project_id: UUID,
        source_id: UUID,
        version_id: UUID,
    ) -> tuple[Source, SourceVersion] | None:
        source = repos.sources.get_for_update(source_id)
        version = repos.versions.get(version_id)
        if (
            source is None
            or version is None
            or source.project_id != project_id
            or version.source_id != source_id
        ):
            logger.warning("index skipped: source %s version %s not found", source_id, version_id)
            return None
        if version.status is VersionStatus.PUBLISHED:
            return None
        if _superseded(source, version):
            logger.info("index skipped: version %s superseded", version_id)
            repos.versions.update(
                replace(
                    version,
                    status=VersionStatus.FAILED,
                    error_message=f"superseded by version {source.current_version}",
                )
            )
            return None
        return source, version

    def _store_chunks(
        self, repos: IndexingRepos, source: Source, version: SourceVersion
    ) -> list[Chunk]:
        repos.chunks.delete_by_version(version.id)
        texts = chunk_text(
            version.extracted_text or "",
            size=self._settings.chunk_size_chars,
            overlap=self._settings.chunk_overlap_chars,
        )
        chunks = [
            Chunk(
                id=chunk_id(version.id, ordinal),
                project_id=source.project_id,
                source_id=source.id,
                version_id=version.id,
                ordinal=ordinal,
                text=text,
            )
            for ordinal, text in enumerate(texts)
        ]
        repos.chunks.add_many(chunks)
        return chunks

    def _index_external(self, chunks: list[Chunk]) -> bool:
        texts = [chunk.text for chunk in chunks]
        self._text.ensure_collection()
        self._text.upsert(chunks)
        vectors = self._embedder.embed(texts)
        if vectors is None:
            return False
        if chunks:
            self._vector.ensure_collection(len(vectors[0]))
            self._vector.upsert(chunks, vectors)
        return True

    def _publish(
        self,
        repos: IndexingRepos,
        source_id: UUID,
        version_id: UUID,
        vector_indexed: bool,
    ) -> list[SourceVersion]:
        """Publishes the version; returns versions whose documents must be dropped from the indexes after commit."""
        source = repos.sources.get_for_update(source_id)
        version = repos.versions.get(version_id)
        if version is None:
            return []
        if source is None:
            return [version]
        if _superseded(source, version):
            repos.chunks.delete_by_version(version.id)
            repos.versions.update(
                replace(
                    version,
                    status=VersionStatus.FAILED,
                    error_message=f"superseded by version {source.current_version}",
                )
            )
            return [version]
        previous = (
            repos.versions.get_by_number(source.id, source.current_version)
            if source.current_version is not None
            else None
        )
        repos.versions.update(
            replace(
                version,
                status=VersionStatus.PUBLISHED,
                vector_indexed=vector_indexed,
                error_message=None,
            )
        )
        repos.sources.update(
            replace(
                source,
                status=SourceStatus.INDEXED,
                current_version=version.version,
                error_message=None,
                updated_at=utcnow(),
            )
        )
        if previous is None:
            return []
        repos.chunks.delete_by_version(previous.id)
        return [previous]

    def _record_failure(
        self, source_id: UUID, version_id: UUID, message: str, final_attempt: bool
    ) -> None:
        dropped: SourceVersion | None = None
        try:
            with self._scope() as repos:
                source = repos.sources.get_for_update(source_id)
                version = repos.versions.get(version_id)
                if source is None or version is None or version.status is VersionStatus.PUBLISHED:
                    return
                repos.chunks.delete_by_version(version.id)
                repos.versions.update(
                    replace(
                        version,
                        status=VersionStatus.FAILED if final_attempt else VersionStatus.PENDING,
                        error_message=message,
                    )
                )
                if source.current_version is None or source.current_version < version.version:
                    repos.sources.update(
                        replace(
                            source,
                            status=SourceStatus.FAILED if final_attempt else source.status,
                            error_message=message,
                            updated_at=utcnow(),
                        )
                    )
                dropped = version
        except Exception:
            logger.exception("could not record indexing failure for version %s", version_id)
        if dropped is not None:
            self._best_effort(lambda: self._discard(dropped))

    def _discard(self, version: SourceVersion) -> None:
        self._text.delete_by_version(version.id)
        self._vector.delete_by_version(version.id)

    @staticmethod
    def _best_effort(action: Callable[[], None]) -> None:
        try:
            action()
        except IndexBackendError:
            logger.warning("index cleanup failed", exc_info=True)
