import math
from collections.abc import Callable
from typing import TypeVar
from uuid import UUID

import httpx
from qdrant_client import QdrantClient
from qdrant_client.http import models
from qdrant_client.http.exceptions import ResponseHandlingException, UnexpectedResponse

from vector_core.domain import Chunk
from vector_core.ports import IndexBackendError

T = TypeVar("T")

PAYLOAD_KEYWORD_FIELDS = ("project_id", "version_id", "source_id")
HTTP_CONFLICT = 409


class QdrantVectorIndex:
    def __init__(
        self,
        url: str,
        timeout: float,
        collection: str = "chunks",
        client: QdrantClient | None = None,
    ) -> None:
        self._collection = collection
        self._client = client or QdrantClient(
            url, timeout=max(1, math.ceil(timeout)), check_compatibility=False
        )

    def ensure_collection(self, dim: int) -> None:
        self._guard("ensure collection", lambda: self._ensure_collection(dim))

    def upsert(self, chunks: list[Chunk], vectors: list[list[float]]) -> None:
        if not chunks:
            return
        points = [
            models.PointStruct(
                id=str(chunk.id),
                vector=vector,
                payload={
                    "project_id": str(chunk.project_id),
                    "version_id": str(chunk.version_id),
                    "source_id": str(chunk.source_id),
                    "text": chunk.text,
                },
            )
            for chunk, vector in zip(chunks, vectors, strict=True)
        ]
        self._guard(
            "upsert",
            lambda: self._client.upsert(self._collection, points, wait=True),
        )

    def delete_by_version(self, version_id: UUID) -> None:
        self._delete_where("version_id", version_id)

    def delete_by_source(self, source_id: UUID) -> None:
        self._delete_where("source_id", source_id)

    def delete_by_project(self, project_id: UUID) -> None:
        self._delete_where("project_id", project_id)

    def search(self, project_id: UUID, vector: list[float], limit: int) -> list[tuple[UUID, float]]:
        def run() -> list[tuple[UUID, float]]:
            if not self._client.collection_exists(self._collection):
                return []
            response = self._client.query_points(
                self._collection,
                query=vector,
                query_filter=self._filter("project_id", project_id),
                limit=limit,
                with_payload=False,
                with_vectors=False,
            )
            return [(UUID(str(point.id)), point.score) for point in response.points]

        return self._guard("search", run)

    def _ensure_collection(self, dim: int) -> None:
        if not self._client.collection_exists(self._collection):
            try:
                self._client.create_collection(
                    collection_name=self._collection,
                    vectors_config=models.VectorParams(size=dim, distance=models.Distance.COSINE),
                )
            except UnexpectedResponse as exc:
                if exc.status_code != HTTP_CONFLICT:
                    raise
        info = self._client.get_collection(self._collection)
        size = info.config.params.vectors.size
        if size != dim:
            raise IndexBackendError(
                f"qdrant collection {self._collection!r} has dimension {size}, expected {dim}"
            )
        for field in PAYLOAD_KEYWORD_FIELDS:
            self._client.create_payload_index(
                collection_name=self._collection,
                field_name=field,
                field_schema=models.PayloadSchemaType.KEYWORD,
            )

    def _delete_where(self, key: str, value: UUID) -> None:
        def run() -> None:
            if not self._client.collection_exists(self._collection):
                return
            self._client.delete(
                collection_name=self._collection,
                points_selector=models.FilterSelector(filter=self._filter(key, value)),
                wait=True,
            )

        self._guard("delete", run)

    @staticmethod
    def _filter(key: str, value: UUID) -> models.Filter:
        return models.Filter(
            must=[models.FieldCondition(key=key, match=models.MatchValue(value=str(value)))]
        )

    @staticmethod
    def _guard(action: str, operation: Callable[[], T]) -> T:
        try:
            return operation()
        except (UnexpectedResponse, ResponseHandlingException, httpx.HTTPError) as exc:
            raise IndexBackendError(f"qdrant {action} failed: {exc}") from exc
