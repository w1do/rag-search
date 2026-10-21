from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from typesense import Client
from typesense.exceptions import ObjectAlreadyExists, ObjectNotFound, TypesenseClientError

from vector_core.domain import Chunk
from vector_core.ports import IndexBackendError

SEARCH_PER_PAGE_LIMIT = 250
FILTER_FIELDS = ("project_id", "version_id", "source_id")
DEFAULT_PORTS = {"http": 80, "https": 443}


def _quote(value: UUID | str) -> str:
    return f"`{value}`"


def _nodes(url: str) -> list[dict[str, Any]]:
    parts = urlsplit(url)
    if parts.hostname is None:
        raise ValueError(f"invalid typesense url: {url!r}")
    return [
        {
            "host": parts.hostname,
            "port": parts.port or DEFAULT_PORTS.get(parts.scheme, 8108),
            "protocol": parts.scheme or "http",
        }
    ]


class TypesenseTextIndex:
    def __init__(
        self,
        url: str,
        api_key: str,
        timeout: float,
        collection: str = "chunks",
        client: Client | None = None,
    ) -> None:
        self._collection = collection
        self._client = client or Client(
            {
                "nodes": _nodes(url),
                "api_key": api_key,
                "connection_timeout_seconds": timeout,
                "num_retries": 1,
            }
        )

    def ensure_collection(self) -> None:
        self._guard("ensure collection", self._ensure_collection)

    def upsert(self, chunks: list[Chunk]) -> None:
        if not chunks:
            return
        documents = [
            {
                "id": str(chunk.id),
                "project_id": str(chunk.project_id),
                "version_id": str(chunk.version_id),
                "source_id": str(chunk.source_id),
                "text": chunk.text,
            }
            for chunk in chunks
        ]

        def run() -> None:
            results = self._documents().import_(documents, {"action": "upsert"})
            failed = [result for result in results if not result.get("success")]
            if failed:
                raise IndexBackendError(f"typesense rejected {len(failed)} documents: {failed[0]}")

        self._guard("upsert", run)

    def delete_by_version(self, version_id: UUID) -> None:
        self._delete_where("version_id", version_id)

    def delete_by_source(self, source_id: UUID) -> None:
        self._delete_where("source_id", source_id)

    def delete_by_project(self, project_id: UUID) -> None:
        self._delete_where("project_id", project_id)

    def search(self, project_id: UUID, query: str, limit: int) -> list[tuple[UUID, float]]:
        def run() -> list[tuple[UUID, float]]:
            response = self._documents().search(
                {
                    "q": query,
                    "query_by": "text",
                    "filter_by": f"project_id:={_quote(project_id)}",
                    "per_page": min(limit, SEARCH_PER_PAGE_LIMIT),
                }
            )
            return [
                (UUID(hit["document"]["id"]), float(hit["text_match"]))
                for hit in response["hits"]
            ]

        try:
            return run()
        except ObjectNotFound:
            return []
        except (TypesenseClientError, httpx.HTTPError) as exc:
            raise IndexBackendError(f"typesense search failed: {exc}") from exc

    def _ensure_collection(self) -> None:
        try:
            self._client.collections[self._collection].retrieve()
        except ObjectNotFound:
            try:
                self._client.collections.create(self._schema())
            except ObjectAlreadyExists:
                return

    def _schema(self) -> dict[str, Any]:
        fields: list[dict[str, Any]] = [
            {"name": field, "type": "string", "facet": True} for field in FILTER_FIELDS
        ]
        fields.append({"name": "text", "type": "string"})
        return {"name": self._collection, "fields": fields}

    def _documents(self) -> Any:
        return self._client.collections[self._collection].documents

    def _delete_where(self, field: str, value: UUID) -> None:
        try:
            self._documents().delete({"filter_by": f"{field}:={_quote(value)}"})
        except ObjectNotFound:
            return
        except (TypesenseClientError, httpx.HTTPError) as exc:
            raise IndexBackendError(f"typesense delete failed: {exc}") from exc

    @staticmethod
    def _guard(action: str, operation: Callable[[], None]) -> None:
        try:
            operation()
        except (TypesenseClientError, httpx.HTTPError) as exc:
            raise IndexBackendError(f"typesense {action} failed: {exc}") from exc
