import os
from collections.abc import Iterator
from uuid import uuid4

import pytest
from qdrant_client import QdrantClient
from typesense import Client as TypesenseClient

from vector_core.domain import Chunk
from vector_core.indexes import QdrantVectorIndex, TypesenseTextIndex

QDRANT_URL = os.environ.get("TEST_QDRANT_URL", "")
TYPESENSE_URL = os.environ.get("TEST_TYPESENSE_URL", "")
TYPESENSE_API_KEY = os.environ.get("TEST_TYPESENSE_API_KEY", "")

pytestmark = pytest.mark.skipif(
    not (QDRANT_URL and TYPESENSE_URL and TYPESENSE_API_KEY),
    reason="TEST_QDRANT_URL, TEST_TYPESENSE_URL and TEST_TYPESENSE_API_KEY are required",
)


def _chunk(project_id, source_id, version_id, ordinal: int, text: str) -> Chunk:
    return Chunk(
        id=uuid4(),
        project_id=project_id,
        source_id=source_id,
        version_id=version_id,
        ordinal=ordinal,
        text=text,
    )


@pytest.fixture
def collection() -> Iterator[str]:
    name = f"chunks_test_{uuid4().hex}"
    yield name
    qdrant = QdrantClient(QDRANT_URL, check_compatibility=False)
    if qdrant.collection_exists(name):
        qdrant.delete_collection(name)
    host_url = TYPESENSE_URL.rstrip("/")
    typesense = TypesenseClient(
        {
            "nodes": [
                {
                    "host": host_url.split("://", 1)[1].split(":")[0],
                    "port": int(host_url.rsplit(":", 1)[1]),
                    "protocol": host_url.split("://", 1)[0],
                }
            ],
            "api_key": TYPESENSE_API_KEY,
            "connection_timeout_seconds": 10,
        }
    )
    try:
        typesense.collections[name].delete()
    except Exception:
        pass


def test_qdrant_index_upserts_searches_and_deletes(collection: str):
    index = QdrantVectorIndex(QDRANT_URL, timeout=10, collection=collection)
    project_id, source_id, version_id = uuid4(), uuid4(), uuid4()
    near = _chunk(project_id, source_id, version_id, 0, "near")
    far = _chunk(project_id, source_id, version_id, 1, "far")
    other = _chunk(uuid4(), uuid4(), uuid4(), 0, "other project")

    index.ensure_collection(3)
    index.ensure_collection(3)
    index.upsert([near, far, other], [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [1.0, 0.0, 0.0]])

    hits = index.search(project_id, [1.0, 0.0, 0.0], 10)
    assert [chunk_id for chunk_id, _ in hits][0] == near.id
    assert other.id not in {chunk_id for chunk_id, _ in hits}

    index.delete_by_version(version_id)
    assert index.search(project_id, [1.0, 0.0, 0.0], 10) == []


def test_typesense_index_upserts_searches_and_deletes(collection: str):
    index = TypesenseTextIndex(TYPESENSE_URL, TYPESENSE_API_KEY, timeout=10, collection=collection)
    project_id, source_id, version_id = uuid4(), uuid4(), uuid4()
    chunk = _chunk(project_id, source_id, version_id, 0, "vector search knowledge base")

    index.ensure_collection()
    index.ensure_collection()
    index.upsert([chunk])

    hits = index.search(project_id, "knowledge", 10)
    assert hits and hits[0][0] == chunk.id

    index.delete_by_source(source_id)
    assert index.search(project_id, "knowledge", 10) == []
