from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.deps import get_embedder, get_engine, get_settings, get_text_index, get_vector_index
from app.main import app
from tests.conftest import OWNER_EMAIL, OWNER_PASSWORD
from vector_core.config import Settings
from vector_core.db.engine import create_db_engine
from vector_core.embeddings import NullEmbedder
from vector_core.indexes.memory import InMemoryTextIndex, InMemoryVectorIndex


@pytest.fixture
def unreachable_db(settings: Settings) -> Iterator[TestClient]:
    url = "postgresql+psycopg://u:p@127.0.0.1:1/x?connect_timeout=1"
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_engine] = lambda: create_db_engine(url)
    app.dependency_overrides[get_vector_index] = lambda: InMemoryVectorIndex()
    app.dependency_overrides[get_text_index] = lambda: InMemoryTextIndex()
    app.dependency_overrides[get_embedder] = lambda: NullEmbedder()
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_login_with_unreachable_database_returns_503_without_dsn(unreachable_db: TestClient):
    response = unreachable_db.post(
        "/api/auth/login", json={"email": OWNER_EMAIL, "password": OWNER_PASSWORD}
    )

    assert response.status_code == 503
    assert response.json() == {
        "detail": {"code": "db_unavailable", "message": "database unavailable"}
    }
    assert "127.0.0.1" not in response.text
    assert "postgresql" not in response.text
