import logging

import httpx

from vector_core.config import Settings
from vector_core.ports import IndexBackendError

DEFAULT_BATCH_SIZE = 64

logger = logging.getLogger(__name__)


class NullEmbedder:
    """Used when no AI key is configured: vectors are unavailable, full-text indexing still works."""

    dim: int | None = None

    def embed(self, texts: list[str]) -> list[list[float]] | None:
        return None


class OpenAICompatibleEmbedder:
    """Calls `POST {base_url}/embeddings` of an OpenAI-compatible API in batches."""

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        dim: int | None = None,
        timeout: float = 30.0,
        batch_size: int = DEFAULT_BATCH_SIZE,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("api_key must not be empty")
        if batch_size < 1:
            raise ValueError("batch_size must be at least 1")
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout = timeout
        self._batch_size = batch_size
        self._transport = transport
        self.dim: int | None = dim

    def embed(self, texts: list[str]) -> list[list[float]] | None:
        if not texts:
            return []
        vectors: list[list[float]] = []
        with httpx.Client(
            timeout=self._timeout,
            transport=self._transport,
            headers={"Authorization": f"Bearer {self._api_key}"},
        ) as client:
            for start in range(0, len(texts), self._batch_size):
                vectors.extend(self._embed_batch(client, texts[start : start + self._batch_size]))
        if self.dim is None and vectors:
            self.dim = len(vectors[0])
        return vectors

    def _embed_batch(self, client: httpx.Client, batch: list[str]) -> list[list[float]]:
        body: dict[str, object] = {
            "model": self._model,
            "input": batch,
            "encoding_format": "float",
        }
        if self.dim is not None:
            body["dimensions"] = self.dim
        try:
            response = client.post(f"{self._base_url}/embeddings", json=body)
        except httpx.HTTPError as exc:
            raise IndexBackendError(f"embeddings request failed: {exc}") from exc
        if response.status_code != 200:
            raise IndexBackendError(
                f"embeddings API returned {response.status_code}: {response.text[:200]}"
            )
        try:
            items = response.json()["data"]
            ordered = sorted(items, key=lambda item: item["index"])
            vectors = [[float(x) for x in item["embedding"]] for item in ordered]
        except (KeyError, TypeError, ValueError) as exc:
            raise IndexBackendError("embeddings API returned an unexpected payload") from exc
        if len(vectors) != len(batch):
            raise IndexBackendError(
                f"embeddings API returned {len(vectors)} vectors for {len(batch)} inputs"
            )
        return vectors


def build_embedder(
    settings: Settings, transport: httpx.BaseTransport | None = None
) -> NullEmbedder | OpenAICompatibleEmbedder:
    if not settings.embeddings_configured:
        return NullEmbedder()
    return OpenAICompatibleEmbedder(
        api_key=settings.ai_api_key,
        base_url=settings.ai_base_url,
        model=settings.ai_embedding_model,
        dim=settings.ai_embedding_dim,
        timeout=settings.index_timeout_seconds,
        transport=transport,
    )
