from vector_core.config import (
    DEFAULT_AI_BASE_URL,
    DEFAULT_AI_EMBEDDING_MODEL,
    Settings,
)


def test_ai_base_url_falls_back_to_ai_api_base():
    settings = Settings.from_env({"AI_API_BASE": "https://polza.ai/api/v1"})

    assert settings.ai_base_url == "https://polza.ai/api/v1"


def test_ai_base_url_prefers_ai_base_url_when_both_set():
    settings = Settings.from_env(
        {
            "AI_BASE_URL": "https://primary.test/v1",
            "AI_API_BASE": "https://polza.ai/api/v1",
        }
    )

    assert settings.ai_base_url == "https://primary.test/v1"


def test_ai_base_url_defaults_when_nothing_set():
    settings = Settings.from_env({})

    assert settings.ai_base_url == DEFAULT_AI_BASE_URL


def test_empty_ai_embedding_model_uses_default():
    settings = Settings.from_env({"AI_EMBEDDING_MODEL": ""})

    assert settings.ai_embedding_model == DEFAULT_AI_EMBEDDING_MODEL
