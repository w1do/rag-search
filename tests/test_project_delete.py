from uuid import UUID, uuid4

import pytest

from tests.fakes import InMemoryStore, build_services
from vector_core.config import Settings
from vector_core.domain import Chunk, NotFound, ServiceUnavailable
from vector_core.indexes.memory import InMemoryTextIndex, InMemoryVectorIndex


def _seed(text: InMemoryTextIndex, vector: InMemoryVectorIndex, project_id: UUID) -> Chunk:
    chunk = Chunk(
        id=uuid4(),
        project_id=project_id,
        source_id=uuid4(),
        version_id=uuid4(),
        ordinal=0,
        text="alpha beta",
    )
    text.ensure_collection()
    text.upsert([chunk])
    vector.ensure_collection(2)
    vector.upsert([chunk], [[1.0, 0.0]])
    return chunk


def test_delete_project_removes_its_documents_and_keeps_other_projects(
    settings: Settings, store: InMemoryStore
):
    text = InMemoryTextIndex()
    vector = InMemoryVectorIndex()
    services = build_services(settings, store, text_index=text, vector_index=vector)
    alpha = services.projects.create("Alpha").id
    beta = services.projects.create("Beta").id
    _seed(text, vector, alpha)
    kept = _seed(text, vector, beta)

    services.projects.delete(alpha)

    assert set(text.docs) == {kept.id}
    assert set(vector.points) == {kept.id}
    with pytest.raises(NotFound):
        services.projects.get(alpha)
    assert services.projects.get(beta).id == beta


@pytest.mark.parametrize("unavailable", ["text", "vector"])
def test_delete_project_is_rejected_when_index_unavailable(
    settings: Settings, store: InMemoryStore, unavailable: str
):
    text = InMemoryTextIndex()
    vector = InMemoryVectorIndex()
    services = build_services(settings, store, text_index=text, vector_index=vector)
    project_id = services.projects.create("Alpha").id
    _seed(text, vector, project_id)
    (text if unavailable == "text" else vector).available = False

    with pytest.raises(ServiceUnavailable) as raised:
        services.projects.delete(project_id)

    assert raised.value.code == "index_unavailable"
    assert services.projects.get(project_id).id == project_id
