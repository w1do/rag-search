import math
import re
from uuid import UUID

from vector_core.domain import Chunk
from vector_core.ports import IndexBackendError

TOKEN_PATTERN = re.compile(r"\w+")


def _unavailable(name: str) -> IndexBackendError:
    return IndexBackendError(f"{name} is unavailable")


def _cosine(left: list[float], right: list[float]) -> float:
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    norm = math.sqrt(sum(a * a for a in left)) * math.sqrt(sum(b * b for b in right))
    return dot / norm if norm else 0.0


class InMemoryVectorIndex:
    def __init__(self, dim: int | None = None) -> None:
        self.dim = dim
        self.available = True
        self.points: dict[UUID, tuple[Chunk, list[float]]] = {}
        self.calls: list[str] = []

    def ensure_collection(self, dim: int) -> None:
        self._check_available("vector index")
        self.calls.append("ensure_collection")
        if self.dim is not None and self.dim != dim:
            raise IndexBackendError(f"vector collection has dimension {self.dim}, expected {dim}")
        self.dim = dim

    def upsert(self, chunks: list[Chunk], vectors: list[list[float]]) -> None:
        self._check_available("vector index")
        self.calls.append("upsert")
        for chunk, vector in zip(chunks, vectors, strict=True):
            self.points[chunk.id] = (chunk, vector)

    def delete_by_version(self, version_id: UUID) -> None:
        self._delete(lambda chunk: chunk.version_id == version_id)

    def delete_by_source(self, source_id: UUID) -> None:
        self._delete(lambda chunk: chunk.source_id == source_id)

    def delete_by_project(self, project_id: UUID) -> None:
        self._delete(lambda chunk: chunk.project_id == project_id)

    def search(self, project_id: UUID, vector: list[float], limit: int) -> list[tuple[UUID, float]]:
        self._check_available("vector index")
        scored = [
            (chunk.id, _cosine(vector, stored))
            for chunk, stored in self.points.values()
            if chunk.project_id == project_id
        ]
        return sorted(scored, key=lambda item: item[1], reverse=True)[:limit]

    def _delete(self, matches) -> None:
        self._check_available("vector index")
        self.calls.append("delete")
        for chunk_id in [cid for cid, (chunk, _) in self.points.items() if matches(chunk)]:
            del self.points[chunk_id]

    def _check_available(self, name: str) -> None:
        if not self.available:
            raise _unavailable(name)


class InMemoryTextIndex:
    def __init__(self) -> None:
        self.available = True
        self.docs: dict[UUID, Chunk] = {}
        self.calls: list[str] = []

    def ensure_collection(self) -> None:
        self._check_available("text index")
        self.calls.append("ensure_collection")

    def upsert(self, chunks: list[Chunk]) -> None:
        self._check_available("text index")
        self.calls.append("upsert")
        for chunk in chunks:
            self.docs[chunk.id] = chunk

    def delete_by_version(self, version_id: UUID) -> None:
        self._delete(lambda chunk: chunk.version_id == version_id)

    def delete_by_source(self, source_id: UUID) -> None:
        self._delete(lambda chunk: chunk.source_id == source_id)

    def delete_by_project(self, project_id: UUID) -> None:
        self._delete(lambda chunk: chunk.project_id == project_id)

    def search(self, project_id: UUID, query: str, limit: int) -> list[tuple[UUID, float]]:
        self._check_available("text index")
        terms = set(TOKEN_PATTERN.findall(query.lower()))
        scored = []
        for chunk in self.docs.values():
            if chunk.project_id != project_id:
                continue
            score = len(terms & set(TOKEN_PATTERN.findall(chunk.text.lower())))
            if score:
                scored.append((chunk.id, float(score)))
        return sorted(scored, key=lambda item: item[1], reverse=True)[:limit]

    def _delete(self, matches) -> None:
        self._check_available("text index")
        self.calls.append("delete")
        for chunk_id in [cid for cid, chunk in self.docs.items() if matches(chunk)]:
            del self.docs[chunk_id]

    def _check_available(self, name: str) -> None:
        if not self.available:
            raise _unavailable(name)
