from vector_core.config import Settings
from vector_core.indexes.qdrant import QdrantVectorIndex
from vector_core.indexes.typesense import TypesenseTextIndex
from vector_core.ports import TextIndex, VectorIndex


def build_indexes(settings: Settings) -> tuple[VectorIndex, TextIndex]:
    vector_index = QdrantVectorIndex(settings.qdrant_url, timeout=settings.index_timeout_seconds)
    text_index = TypesenseTextIndex(
        settings.typesense_url,
        settings.typesense_api_key,
        timeout=settings.index_timeout_seconds,
    )
    return vector_index, text_index


__all__ = ["QdrantVectorIndex", "TypesenseTextIndex", "build_indexes"]
