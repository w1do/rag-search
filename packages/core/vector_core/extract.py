import io
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
from docx import Document
from docx.opc.exceptions import PackageNotFoundError
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from vector_core.config import Settings
from vector_core.domain import PayloadTooLarge, UnsupportedFormat, ValidationError

SUPPORTED_EXTENSIONS = {"txt", "md", "pdf", "docx"}
TEXT_EXTENSIONS = {"txt", "md"}
TEXT_MEDIA_TYPES = {"text/plain", "text/markdown"}
LINK_SCHEMES = {"http", "https"}
FIRECRAWL_SCRAPE_PATH = "/v2/scrape"
FIRECRAWL_MIN_TIMEOUT_MS = 1000
FIRECRAWL_MAX_TIMEOUT_MS = 300000
FIRECRAWL_CLIENT_GRACE_SECONDS = 10.0
FIRECRAWL_UNAVAILABLE = "url could not be fetched via firecrawl"


def _media_type(content_type: str | None) -> str:
    return (content_type or "").split(";", 1)[0].strip().lower()


def _decode(data: bytes) -> str:
    return data.decode("utf-8", errors="replace")


def _extract_pdf(data: bytes) -> str:
    try:
        reader = PdfReader(io.BytesIO(data))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    except PdfReadError as exc:
        raise UnsupportedFormat("invalid pdf file") from exc


def _extract_docx(data: bytes) -> str:
    try:
        document = Document(io.BytesIO(data))
    except (PackageNotFoundError, KeyError, ValueError) as exc:
        raise UnsupportedFormat("invalid docx file") from exc
    return "\n".join(paragraph.text for paragraph in document.paragraphs)


def extract_text(data: bytes, content_type: str | None, filename: str | None) -> str:
    extension = Path(filename).suffix.lower().lstrip(".") if filename else ""
    if extension in TEXT_EXTENSIONS:
        return _decode(data)
    if extension == "pdf":
        return _extract_pdf(data)
    if extension == "docx":
        return _extract_docx(data)
    media_type = _media_type(content_type)
    if media_type in TEXT_MEDIA_TYPES:
        return _decode(data)
    raise UnsupportedFormat("unsupported content type")


class StandardContentExtractor:
    def extract(self, data: bytes, content_type: str | None, filename: str | None) -> str:
        return extract_text(data, content_type, filename)


@dataclass(frozen=True)
class FetchedLink:
    markdown: str
    title: str | None = None


def check_link_url(url: str) -> None:
    if urlsplit(url).scheme.lower() not in LINK_SCHEMES:
        raise ValidationError("only http and https urls are supported")


def _firecrawl_timeout_ms(timeout: float) -> int:
    return max(FIRECRAWL_MIN_TIMEOUT_MS, min(FIRECRAWL_MAX_TIMEOUT_MS, int(timeout * 1000)))


def _page_title(metadata: dict[str, Any]) -> str | None:
    title = metadata.get("title")
    if isinstance(title, list):
        title = next((item for item in title if isinstance(item, str)), None)
    if isinstance(title, str) and title.strip():
        return title
    return None


def _page_status(metadata: dict[str, Any]) -> int | None:
    status = metadata.get("statusCode")
    return status if isinstance(status, int) and not isinstance(status, bool) else None


class FirecrawlLinkFetcher:
    """Fetch one page as Markdown through the Firecrawl Scrape API v2; the key never leaves headers."""

    def __init__(
        self,
        api_key: str,
        base_url: str,
        timeout: float,
        max_bytes: int,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("firecrawl api key is required")
        self._api_key = api_key
        self._endpoint = base_url.rstrip("/") + FIRECRAWL_SCRAPE_PATH
        self._timeout = timeout
        self._max_bytes = max_bytes
        self._transport = transport

    def fetch(self, url: str) -> FetchedLink:
        check_link_url(url)
        body = {
            "url": url,
            "formats": ["markdown"],
            "onlyMainContent": True,
            "timeout": _firecrawl_timeout_ms(self._timeout),
        }
        try:
            with httpx.Client(
                timeout=self._timeout + FIRECRAWL_CLIENT_GRACE_SECONDS,
                transport=self._transport,
                headers={"Authorization": f"Bearer {self._api_key}"},
            ) as client:
                response = client.post(self._endpoint, json=body)
        except httpx.HTTPError as exc:
            raise ValidationError(FIRECRAWL_UNAVAILABLE) from exc
        self._check_status(response.status_code)
        try:
            payload = response.json()
        except ValueError as exc:
            raise ValidationError(FIRECRAWL_UNAVAILABLE) from exc
        return self._parse(payload, response.is_success)

    def _check_status(self, status: int) -> None:
        if status == 402:
            raise ValidationError("firecrawl: credits exhausted")
        if status == 429:
            raise ValidationError("firecrawl: rate limit exceeded")
        if status in {401, 403}:
            raise ValidationError("firecrawl: request rejected (check FIRECRAWL_API_KEY)")
        if status >= 500:
            raise ValidationError(FIRECRAWL_UNAVAILABLE)

    def _parse(self, payload: Any, ok: bool) -> FetchedLink:
        if not isinstance(payload, dict):
            raise ValidationError(FIRECRAWL_UNAVAILABLE)
        data = payload.get("data")
        data = data if isinstance(data, dict) else {}
        metadata = data.get("metadata")
        metadata = metadata if isinstance(metadata, dict) else {}
        page_status = _page_status(metadata)
        if page_status is not None and page_status >= 400:
            raise ValidationError(f"url returned status {page_status}")
        if not ok or payload.get("success") is not True:
            if page_status is not None:
                raise ValidationError(f"url returned status {page_status}")
            raise ValidationError(FIRECRAWL_UNAVAILABLE)
        markdown = data.get("markdown")
        if not isinstance(markdown, str) or not markdown.strip():
            raise ValidationError("no content extracted from url")
        if len(markdown.encode("utf-8")) > self._max_bytes:
            raise PayloadTooLarge("remote content exceeds size limit")
        return FetchedLink(markdown=markdown, title=_page_title(metadata))


def build_link_fetcher(
    settings: Settings, *, transport: httpx.BaseTransport | None = None
) -> FirecrawlLinkFetcher | None:
    """Links are ingested only through Firecrawl; without a key link ingestion is disabled."""
    if not settings.link_ingestion_configured:
        return None
    return FirecrawlLinkFetcher(
        settings.firecrawl_api_key,
        settings.firecrawl_base_url,
        settings.firecrawl_timeout_seconds,
        settings.max_upload_bytes,
        transport=transport,
    )
