import json

import httpx
import pytest

from vector_core.config import Settings
from vector_core.embeddings import NullEmbedder, OpenAICompatibleEmbedder, build_embedder
from vector_core.ports import IndexBackendError


def _embedder(handler, **kwargs) -> OpenAICompatibleEmbedder:
    options = {
        "api_key": "test-key",
        "base_url": "https://embeddings.test/v1/",
        "model": "test-model",
        "transport": httpx.MockTransport(handler),
        **kwargs,
    }
    return OpenAICompatibleEmbedder(**options)


def test_embed_batches_requests_and_keeps_input_order():
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        bodies.append(body)
        data = [
            {"index": position, "embedding": [float(len(text))]}
            for position, text in reversed(list(enumerate(body["input"])))
        ]
        return httpx.Response(200, json={"data": data})

    embedder = _embedder(handler, batch_size=2)

    vectors = embedder.embed(["a", "bb", "ccc"])

    assert vectors == [[1.0], [2.0], [3.0]]
    assert [len(body["input"]) for body in bodies] == [2, 1]
    assert all(body["model"] == "test-model" for body in bodies)
    assert embedder.dim == 1


def test_embed_sends_dimensions_only_when_configured():
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append(body)
        return httpx.Response(
            200, json={"data": [{"index": 0, "embedding": [0.1, 0.2]}]}
        )

    _embedder(handler).embed(["text"])
    assert "dimensions" not in seen[-1]

    _embedder(handler, dim=2).embed(["text"])
    assert seen[-1]["dimensions"] == 2


def test_embed_uses_bearer_key_and_normalized_endpoint():
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0]}]})

    _embedder(handler).embed(["text"])

    assert str(captured[0].url) == "https://embeddings.test/v1/embeddings"
    assert captured[0].headers["Authorization"] == "Bearer test-key"


def test_embed_empty_input_makes_no_request():
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no request expected")

    assert _embedder(handler).embed([]) == []


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(429, text="rate limited"),
        httpx.Response(200, json={"data": [{"index": 0}]}),
        httpx.Response(200, json={"data": []}),
        httpx.Response(200, text="not json"),
    ],
)
def test_bad_responses_raise_index_backend_error(response):
    embedder = _embedder(lambda request: response)

    with pytest.raises(IndexBackendError):
        embedder.embed(["text"])


def test_network_failure_raises_index_backend_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    with pytest.raises(IndexBackendError):
        _embedder(handler).embed(["text"])


def test_build_embedder_without_key_returns_null_embedder():
    settings = Settings(database_url="", email="", password="", mcp_token="")

    embedder = build_embedder(settings)

    assert isinstance(embedder, NullEmbedder)
    assert embedder.embed(["text"]) is None


def test_build_embedder_with_key_returns_openai_compatible_embedder():
    settings = Settings(
        database_url="",
        email="",
        password="",
        mcp_token="",
        ai_api_key="test-key",
        ai_embedding_dim=8,
    )

    embedder = build_embedder(settings)

    assert isinstance(embedder, OpenAICompatibleEmbedder)
    assert embedder.dim == 8
