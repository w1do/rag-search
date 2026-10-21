import io
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from docx import Document
from docx.opc.exceptions import PackageNotFoundError
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from vector_core.domain import PayloadTooLarge, UnsupportedFormat, ValidationError

SUPPORTED_EXTENSIONS = {"txt", "md", "pdf", "docx"}
TEXT_EXTENSIONS = {"txt", "md"}
TEXT_MEDIA_TYPES = {"text/plain", "text/markdown"}
HTML_MEDIA_TYPE = "text/html"
SKIPPED_HTML_TAGS = {"script", "style"}


class _HtmlTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._skip_depth = 0
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in SKIPPED_HTML_TAGS:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in SKIPPED_HTML_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0:
            self._parts.append(data)

    def text(self) -> str:
        return " ".join(" ".join(self._parts).split())


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


def _extract_html(data: bytes) -> str:
    parser = _HtmlTextParser()
    parser.feed(_decode(data))
    parser.close()
    return parser.text()


def extract_text(data: bytes, content_type: str | None, filename: str | None) -> str:
    extension = Path(filename).suffix.lower().lstrip(".") if filename else ""
    if extension in TEXT_EXTENSIONS:
        return _decode(data)
    if extension == "pdf":
        return _extract_pdf(data)
    if extension == "docx":
        return _extract_docx(data)
    media_type = _media_type(content_type)
    if media_type == HTML_MEDIA_TYPE:
        return _extract_html(data)
    if media_type in TEXT_MEDIA_TYPES:
        return _decode(data)
    raise UnsupportedFormat("unsupported content type")


class StandardContentExtractor:
    def extract(self, data: bytes, content_type: str | None, filename: str | None) -> str:
        return extract_text(data, content_type, filename)


def fetch_url(
    url: str,
    timeout: float,
    max_bytes: int,
    *,
    transport: httpx.BaseTransport | None = None,
) -> tuple[bytes, str | None]:
    if urlsplit(url).scheme.lower() not in {"http", "https"}:
        raise ValidationError("only http and https urls are supported")
    try:
        with (
            httpx.Client(follow_redirects=True, timeout=timeout, transport=transport) as client,
            client.stream("GET", url) as response,
        ):
            response.raise_for_status()
            declared = response.headers.get("content-length")
            if declared is not None and int(declared) > max_bytes:
                raise PayloadTooLarge("remote content exceeds size limit")
            body = bytearray()
            for chunk in response.iter_bytes():
                body.extend(chunk)
                if len(body) > max_bytes:
                    raise PayloadTooLarge("remote content exceeds size limit")
            return bytes(body), response.headers.get("content-type")
    except httpx.HTTPError as exc:
        raise ValidationError("url could not be fetched") from exc
