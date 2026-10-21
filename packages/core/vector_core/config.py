import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_SESSION_TTL_SECONDS = 12 * 60 * 60
DEFAULT_JOB_LEASE_SECONDS = 30
DEFAULT_JOB_POLL_INTERVAL_SECONDS = 1.0
DEFAULT_JOB_MAX_ATTEMPTS = 5
DEFAULT_JOB_BACKOFF_BASE_SECONDS = 5.0
DEFAULT_JOB_BACKOFF_MAX_SECONDS = 300.0
DEFAULT_BLOB_DIR = "/data/blobs"
DEFAULT_MAX_UPLOAD_BYTES = 10485760
DEFAULT_FIRECRAWL_BASE_URL = "https://api.firecrawl.dev"
DEFAULT_FIRECRAWL_TIMEOUT_SECONDS = 60.0
DEFAULT_CHUNK_SIZE_CHARS = 800
DEFAULT_CHUNK_OVERLAP_CHARS = 100
DEFAULT_QDRANT_URL = "http://qdrant:6333"
DEFAULT_TYPESENSE_URL = "http://typesense:8108"
DEFAULT_AI_BASE_URL = "https://api.openai.com/v1"
DEFAULT_AI_EMBEDDING_MODEL = "text-embedding-3-small"
DEFAULT_SEARCH_TIMEOUT_SECONDS = 5.0
DEFAULT_INDEX_TIMEOUT_SECONDS = 30.0


def normalize_database_url(url: str) -> str:
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url.removeprefix("postgresql://")
    return url


@dataclass(frozen=True)
class Settings:
    database_url: str = field(repr=False)
    email: str = field(repr=False)
    password: str = field(repr=False)
    mcp_token: str = field(repr=False)
    session_ttl_seconds: int = DEFAULT_SESSION_TTL_SECONDS
    job_lease_seconds: int = DEFAULT_JOB_LEASE_SECONDS
    job_poll_interval_seconds: float = DEFAULT_JOB_POLL_INTERVAL_SECONDS
    job_max_attempts: int = DEFAULT_JOB_MAX_ATTEMPTS
    job_backoff_base_seconds: float = DEFAULT_JOB_BACKOFF_BASE_SECONDS
    job_backoff_max_seconds: float = DEFAULT_JOB_BACKOFF_MAX_SECONDS
    blob_dir: Path = Path(DEFAULT_BLOB_DIR)
    max_upload_bytes: int = DEFAULT_MAX_UPLOAD_BYTES
    chunk_size_chars: int = DEFAULT_CHUNK_SIZE_CHARS
    chunk_overlap_chars: int = DEFAULT_CHUNK_OVERLAP_CHARS
    qdrant_url: str = DEFAULT_QDRANT_URL
    typesense_url: str = DEFAULT_TYPESENSE_URL
    typesense_api_key: str = field(default="", repr=False)
    ai_api_key: str = field(default="", repr=False)
    ai_base_url: str = DEFAULT_AI_BASE_URL
    ai_embedding_model: str = DEFAULT_AI_EMBEDDING_MODEL
    ai_embedding_dim: int | None = None
    search_timeout_seconds: float = DEFAULT_SEARCH_TIMEOUT_SECONDS
    index_timeout_seconds: float = DEFAULT_INDEX_TIMEOUT_SECONDS
    firecrawl_api_key: str = field(default="", repr=False)
    firecrawl_base_url: str = DEFAULT_FIRECRAWL_BASE_URL
    firecrawl_timeout_seconds: float = DEFAULT_FIRECRAWL_TIMEOUT_SECONDS

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Settings":
        source = os.environ if env is None else env
        return cls(
            database_url=normalize_database_url(source.get("DATABASE_URL", "")),
            email=source.get("EMAIL", ""),
            password=source.get("PASSWORD", ""),
            mcp_token=source.get("MCP_TOKEN", ""),
            session_ttl_seconds=int(source.get("SESSION_TTL_SECONDS", DEFAULT_SESSION_TTL_SECONDS)),
            job_lease_seconds=int(source.get("JOB_LEASE_SECONDS", DEFAULT_JOB_LEASE_SECONDS)),
            job_poll_interval_seconds=float(
                source.get("JOB_POLL_INTERVAL_SECONDS", DEFAULT_JOB_POLL_INTERVAL_SECONDS)
            ),
            job_max_attempts=int(source.get("JOB_MAX_ATTEMPTS", DEFAULT_JOB_MAX_ATTEMPTS)),
            job_backoff_base_seconds=float(
                source.get("JOB_BACKOFF_BASE_SECONDS", DEFAULT_JOB_BACKOFF_BASE_SECONDS)
            ),
            job_backoff_max_seconds=float(
                source.get("JOB_BACKOFF_MAX_SECONDS", DEFAULT_JOB_BACKOFF_MAX_SECONDS)
            ),
            blob_dir=Path(source.get("BLOB_DIR", DEFAULT_BLOB_DIR)),
            max_upload_bytes=int(source.get("MAX_UPLOAD_BYTES", DEFAULT_MAX_UPLOAD_BYTES)),
            chunk_size_chars=int(source.get("CHUNK_SIZE_CHARS", DEFAULT_CHUNK_SIZE_CHARS)),
            chunk_overlap_chars=int(source.get("CHUNK_OVERLAP_CHARS", DEFAULT_CHUNK_OVERLAP_CHARS)),
            qdrant_url=source.get("QDRANT_URL", DEFAULT_QDRANT_URL),
            typesense_url=source.get("TYPESENSE_URL", DEFAULT_TYPESENSE_URL),
            typesense_api_key=source.get("TYPESENSE_API_KEY", ""),
            ai_api_key=source.get("AI_API_KEY", ""),
            ai_base_url=source.get("AI_BASE_URL") or source.get("AI_API_BASE") or DEFAULT_AI_BASE_URL,
            ai_embedding_model=source.get("AI_EMBEDDING_MODEL") or DEFAULT_AI_EMBEDDING_MODEL,
            ai_embedding_dim=_optional_int(source.get("AI_EMBEDDING_DIM")),
            search_timeout_seconds=float(
                source.get("SEARCH_TIMEOUT_SECONDS", DEFAULT_SEARCH_TIMEOUT_SECONDS)
            ),
            index_timeout_seconds=float(
                source.get("INDEX_TIMEOUT_SECONDS", DEFAULT_INDEX_TIMEOUT_SECONDS)
            ),
            firecrawl_api_key=source.get("FIRECRAWL_API_KEY", ""),
            firecrawl_base_url=source.get("FIRECRAWL_BASE_URL") or DEFAULT_FIRECRAWL_BASE_URL,
            firecrawl_timeout_seconds=float(
                source.get("FIRECRAWL_TIMEOUT_SECONDS") or DEFAULT_FIRECRAWL_TIMEOUT_SECONDS
            ),
        )

    @property
    def auth_configured(self) -> bool:
        return bool(self.email and self.password)

    @property
    def embeddings_configured(self) -> bool:
        return bool(self.ai_api_key)

    @property
    def link_ingestion_configured(self) -> bool:
        return bool(self.firecrawl_api_key)


def _optional_int(value: str | None) -> int | None:
    return int(value) if value else None
