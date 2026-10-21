import logging
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from uuid import UUID

from vector_core.config import Settings
from vector_core.domain import (
    TEXT_SEARCH_UNAVAILABLE,
    VECTOR_SEARCH_UNAVAILABLE,
    ContextResult,
    ContextSource,
    NotFound,
    ServiceUnavailable,
    Source,
    SearchHit,
    SearchResult,
    ValidationError,
    SourceVersion,
    VersionStatus,
)
from vector_core.ports import Embedder, TextIndex, VectorIndex
from vector_core.repositories import (
    ChunkRepository,
    ProjectRepository,
    SourceRepository,
    VersionRepository,
)

RRF_K = 60
CANDIDATE_FACTOR = 3
MAX_CANDIDATES = 50
MAX_LIMIT = 50
DEFAULT_LIMIT = 10
CONTEXT_LIMIT = 10
DEFAULT_MAX_CHARS = 4000
SEARCH_UNAVAILABLE = "search_unavailable"

logger = logging.getLogger(__name__)

_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="search")


def rrf_fuse(rankings: list[list[UUID]], k: int = RRF_K) -> list[tuple[UUID, float]]:
    scores: dict[UUID, float] = {}
    for ranking in rankings:
        for rank, item in enumerate(ranking, start=1):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda pair: pair[1], reverse=True)


class SearchService:
    def __init__(
        self,
        settings: Settings,
        projects: ProjectRepository,
        sources: SourceRepository,
        versions: VersionRepository,
        chunks: ChunkRepository,
        text_index: TextIndex,
        vector_index: VectorIndex,
        embedder: Embedder,
        executor: ThreadPoolExecutor | None = None,
    ) -> None:
        self._settings = settings
        self._projects = projects
        self._sources = sources
        self._versions = versions
        self._chunks = chunks
        self._text = text_index
        self._vector = vector_index
        self._embedder = embedder
        self._executor = executor if executor is not None else _EXECUTOR

    def search(self, project_id: UUID, query: str, limit: int = DEFAULT_LIMIT) -> SearchResult:
        if self._projects.get(project_id) is None:
            raise NotFound("project not found")
        text = query.strip()
        if not text:
            raise ValidationError("query must not be empty")
        if not 1 <= limit <= MAX_LIMIT:
            raise ValidationError(f"limit must be between 1 and {MAX_LIMIT}")
        candidates = min(limit * CANDIDATE_FACTOR, MAX_CANDIDATES)
        vector_future = self._executor.submit(self._vector_ranking, project_id, text, candidates)
        text_ranking = self._text_ranking(project_id, text, candidates)
        vector_ranking = self._collect_vector(vector_future)
        if vector_ranking is None and text_ranking is None:
            raise ServiceUnavailable("search backends are unavailable", code=SEARCH_UNAVAILABLE)
        rankings = [ranking for ranking in (vector_ranking, text_ranking) if ranking is not None]
        warning = None
        if vector_ranking is None:
            warning = VECTOR_SEARCH_UNAVAILABLE
        elif text_ranking is None:
            warning = TEXT_SEARCH_UNAVAILABLE
        hits = self._visible_hits(project_id, rrf_fuse(rankings), limit)
        return SearchResult(hits=hits, warning=warning)

    def context(
        self, project_id: UUID, query: str, max_chars: int = DEFAULT_MAX_CHARS
    ) -> ContextResult:
        result = self.search(project_id, query, limit=CONTEXT_LIMIT)
        blocks: list[str] = []
        sources: list[ContextSource] = []
        seen: set[tuple[UUID, int]] = set()
        used = 0
        for index, hit in enumerate(result.hits, start=1):
            remaining = max_chars - used
            if remaining <= 0:
                break
            block = (
                f"[{index}] {hit.title} (v{hit.version}, score {hit.score:.4f})\n"
                f"{hit.text}\n\n"
            )[:remaining]
            blocks.append(block)
            used += len(block)
            if (hit.source_id, hit.version) not in seen:
                seen.add((hit.source_id, hit.version))
                sources.append(ContextSource(hit.source_id, hit.title, hit.version))
        return ContextResult(context="".join(blocks), sources=sources, warning=result.warning)

    def _vector_ranking(self, project_id: UUID, text: str, candidates: int) -> list[UUID]:
        vectors = self._embedder.embed([text])
        if not vectors:
            raise ServiceUnavailable("embeddings are unavailable", code=VECTOR_SEARCH_UNAVAILABLE)
        return [chunk_id for chunk_id, _ in self._vector.search(project_id, vectors[0], candidates)]

    def _text_ranking(self, project_id: UUID, text: str, candidates: int) -> list[UUID] | None:
        try:
            return [chunk_id for chunk_id, _ in self._text.search(project_id, text, candidates)]
        except Exception:
            logger.warning("full-text search failed", exc_info=True)
            return None

    def _collect_vector(self, future: Future[list[UUID]]) -> list[UUID] | None:
        try:
            return future.result(timeout=self._settings.search_timeout_seconds)
        except FutureTimeoutError:
            future.cancel()
            logger.warning("vector search timed out")
        except Exception:
            logger.warning("vector search failed", exc_info=True)
        return None

    def _visible_hits(
        self, project_id: UUID, ranked: list[tuple[UUID, float]], limit: int
    ) -> list[SearchHit]:
        chunks = {chunk.id: chunk for chunk in self._chunks.get_many([cid for cid, _ in ranked])}
        sources: dict[UUID, Source | None] = {}
        versions: dict[UUID, SourceVersion | None] = {}
        hits: list[SearchHit] = []
        for chunk_id, score in ranked:
            if len(hits) >= limit:
                break
            chunk = chunks.get(chunk_id)
            if chunk is None or chunk.project_id != project_id:
                continue
            if chunk.source_id not in sources:
                sources[chunk.source_id] = self._sources.get(chunk.source_id)
            source = sources[chunk.source_id]
            if chunk.version_id not in versions:
                versions[chunk.version_id] = self._versions.get(chunk.version_id)
            version = versions[chunk.version_id]
            if (
                source is None
                or source.project_id != project_id
                or source.current_version is None
                or version is None
                or version.source_id != source.id
                or version.status is not VersionStatus.PUBLISHED
                or version.version != source.current_version
            ):
                continue
            hits.append(
                SearchHit(
                    source_id=source.id,
                    title=source.title,
                    version=version.version,
                    text=chunk.text,
                    score=score,
                    locator=dict(chunk.locator),
                )
            )
        return hits
