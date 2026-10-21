import pytest

from vector_core.config import (
    DEFAULT_AI_BASE_URL,
    DEFAULT_AI_EMBEDDING_MODEL,
    DEFAULT_FIRECRAWL_BASE_URL,
    DEFAULT_FIRECRAWL_TIMEOUT_SECONDS,
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


def test_firecrawl_settings_default_when_nothing_set():
    settings = Settings.from_env({})

    assert settings.firecrawl_api_key == ""
    assert settings.firecrawl_base_url == DEFAULT_FIRECRAWL_BASE_URL
    assert settings.firecrawl_timeout_seconds == DEFAULT_FIRECRAWL_TIMEOUT_SECONDS
    assert settings.link_ingestion_configured is False


def test_firecrawl_settings_are_read_from_env_and_key_is_hidden():
    settings = Settings.from_env(
        {
            "FIRECRAWL_API_KEY": "fc-secret-value",
            "FIRECRAWL_BASE_URL": "https://firecrawl.internal",
            "FIRECRAWL_TIMEOUT_SECONDS": "90",
        }
    )

    assert settings.firecrawl_api_key == "fc-secret-value"
    assert settings.firecrawl_base_url == "https://firecrawl.internal"
    assert settings.firecrawl_timeout_seconds == 90.0
    assert settings.link_ingestion_configured is True
    assert "fc-secret-value" not in repr(settings)


def test_swagger_is_disabled_by_default():
    assert Settings.from_env({}).swagger_enabled is False


def test_swagger_is_enabled_by_env_true():
    assert Settings.from_env({"SWAGGER": "true"}).swagger_enabled is True
    assert Settings.from_env({"SWAGGER": "False"}).swagger_enabled is False


def test_swagger_rejects_invalid_value():
    with pytest.raises(ValueError):
        Settings.from_env({"SWAGGER": "yes"})
