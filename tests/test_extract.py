import io
import json

import httpx
import pytest
from docx import Document
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
from vector_core.chunking import chunk_text
from vector_core.config import Settings
from vector_core.domain import PayloadTooLarge, UnsupportedFormat, ValidationError
from vector_core.extract import (
    SUPPORTED_EXTENSIONS,
    FirecrawlLinkFetcher,
    build_link_fetcher,
    extract_text,
)

FIRECRAWL_KEY = "fc-test-secret-key-123"
PAGE_URL = "https://example.test/page"


def make_pdf(text: str) -> bytes:
    writer = PdfWriter()
    page = writer.add_blank_page(width=300, height=300)
    font = writer._add_object(
        DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            }
        )
    )
    stream = DecodedStreamObject()
    stream.set_data(f"BT /F1 12 Tf 20 150 Td ({text}) Tj ET".encode())
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})}
    )
    page[NameObject("/Contents")] = writer._add_object(stream)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def firecrawl_ok(markdown: str, title: str | None = "Page Title", status: int = 200) -> dict:
    metadata: dict = {"sourceURL": PAGE_URL, "statusCode": status}
    if title is not None:
        metadata["title"] = title
    return {"success": True, "data": {"markdown": markdown, "metadata": metadata}}


def make_fetcher(handler, timeout: float = 60.0, max_bytes: int = 1000) -> FirecrawlLinkFetcher:
    return FirecrawlLinkFetcher(
        FIRECRAWL_KEY,
        "https://firecrawl.test/",
        timeout,
        max_bytes,
        transport=httpx.MockTransport(handler),
    )


def make_docx(*paragraphs: str) -> bytes:
    document = Document()
    for paragraph in paragraphs:
        document.add_paragraph(paragraph)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def test_supported_extensions_are_fixed():
    assert SUPPORTED_EXTENSIONS == {"txt", "md", "pdf", "docx"}


def test_txt_and_md_are_decoded_as_utf8():
    assert extract_text("héllo".encode(), None, "note.txt") == "héllo"
    assert extract_text(b"# Title", None, "readme.md") == "# Title"


def test_invalid_utf8_is_replaced_not_rejected():
    assert extract_text(b"ok\xff", None, "a.txt") == "ok\ufffd"


def test_pdf_text_is_extracted():
    assert "Hello PDF" in extract_text(make_pdf("Hello PDF"), None, "doc.pdf")


def test_invalid_pdf_is_unsupported():
    with pytest.raises(UnsupportedFormat):
        extract_text(b"not a pdf", None, "broken.pdf")


def test_docx_paragraphs_are_extracted():
    assert extract_text(make_docx("First", "Second"), None, "doc.docx") == "First\nSecond"


def test_plain_text_content_type_is_decoded_without_filename():
    assert extract_text(b"plain", "text/plain", None) == "plain"


def test_unknown_format_is_unsupported():
    with pytest.raises(UnsupportedFormat):
        extract_text(b"\x89PNG", "image/png", "picture.png")


def test_firecrawl_posts_scrape_request_with_bearer_key():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        seen["timeout"] = request.extensions["timeout"]
        return httpx.Response(200, json=firecrawl_ok("# Title\n\nBody"))

    fetched = make_fetcher(handler, timeout=45).fetch(PAGE_URL)

    assert seen["method"] == "POST"
    assert seen["url"] == "https://firecrawl.test/v2/scrape"
    assert seen["auth"] == f"Bearer {FIRECRAWL_KEY}"
    assert seen["body"] == {
        "url": PAGE_URL,
        "formats": ["markdown"],
        "onlyMainContent": True,
        "timeout": 45000,
    }
    assert seen["timeout"]["read"] == 55
    assert fetched.markdown == "# Title\n\nBody"
    assert fetched.title == "Page Title"


@pytest.mark.parametrize(("timeout", "expected"), [(0.2, 1000), (60, 60000), (900, 300000)])
def test_firecrawl_timeout_is_clamped(timeout, expected):
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["timeout"] = json.loads(request.content)["timeout"]
        return httpx.Response(200, json=firecrawl_ok("text"))

    make_fetcher(handler, timeout=timeout).fetch(PAGE_URL)

    assert seen["timeout"] == expected


def test_firecrawl_missing_title_is_none():
    fetcher = make_fetcher(lambda request: httpx.Response(200, json=firecrawl_ok("text", title=None)))

    assert fetcher.fetch(PAGE_URL).title is None


def test_firecrawl_rejects_non_http_scheme_without_request():
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=firecrawl_ok("text"))

    with pytest.raises(ValidationError, match="only http and https"):
        make_fetcher(handler).fetch("ftp://example.test/file.txt")
    assert calls == []


def _raise_timeout(request: httpx.Request) -> httpx.Response:
    raise httpx.ReadTimeout(f"timed out {request.headers['authorization']}", request=request)


def _raise_connect(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("connection refused", request=request)


@pytest.mark.parametrize(
    ("handler", "error", "message"),
    [
        (lambda r: httpx.Response(402, json={"success": False}), ValidationError, "firecrawl: credits exhausted"),
        (lambda r: httpx.Response(429, json={"success": False}), ValidationError, "firecrawl: rate limit exceeded"),
        (
            lambda r: httpx.Response(401, json={"error": f"bad key {FIRECRAWL_KEY}"}),
            ValidationError,
            "firecrawl: request rejected (check FIRECRAWL_API_KEY)",
        ),
        (
            lambda r: httpx.Response(403, json={"success": False}),
            ValidationError,
            "firecrawl: request rejected (check FIRECRAWL_API_KEY)",
        ),
        (lambda r: httpx.Response(500, text="oops"), ValidationError, "url could not be fetched via firecrawl"),
        (lambda r: httpx.Response(503, json={}), ValidationError, "url could not be fetched via firecrawl"),
        (_raise_timeout, ValidationError, "url could not be fetched via firecrawl"),
        (_raise_connect, ValidationError, "url could not be fetched via firecrawl"),
        (lambda r: httpx.Response(200, text="not json"), ValidationError, "url could not be fetched via firecrawl"),
        (
            lambda r: httpx.Response(200, json={"success": False, "error": "blocked"}),
            ValidationError,
            "url could not be fetched via firecrawl",
        ),
        (
            lambda r: httpx.Response(
                200, json={"success": False, "data": {"metadata": {"statusCode": 410}}}
            ),
            ValidationError,
            "url returned status 410",
        ),
        (lambda r: httpx.Response(200, json=firecrawl_ok("gone", status=404)), ValidationError, "url returned status 404"),
        (lambda r: httpx.Response(200, json=firecrawl_ok("   \n\t")), ValidationError, "no content extracted from url"),
        (
            lambda r: httpx.Response(200, json={"success": True, "data": {"metadata": {}}}),
            ValidationError,
            "no content extracted from url",
        ),
        (lambda r: httpx.Response(200, json=firecrawl_ok("x" * 1001)), PayloadTooLarge, "remote content exceeds size limit"),
    ],
)
def test_firecrawl_errors_are_mapped_without_leaking_key(handler, error, message):
    with pytest.raises(error) as excinfo:
        make_fetcher(handler).fetch(PAGE_URL)

    assert str(excinfo.value) == message
    assert FIRECRAWL_KEY not in str(excinfo.value)
    assert FIRECRAWL_KEY not in repr(excinfo.value)


def test_firecrawl_size_limit_counts_utf8_bytes():
    fetcher = make_fetcher(lambda r: httpx.Response(200, json=firecrawl_ok("я" * 501)))

    with pytest.raises(PayloadTooLarge):
        fetcher.fetch(PAGE_URL)


def test_build_link_fetcher_requires_firecrawl_key():
    settings = Settings(database_url="", email="", password="", mcp_token="")

    assert build_link_fetcher(settings) is None
    configured = Settings(
        database_url="", email="", password="", mcp_token="", firecrawl_api_key=FIRECRAWL_KEY
    )
    assert isinstance(build_link_fetcher(configured), FirecrawlLinkFetcher)


def test_chunk_text_returns_nothing_for_blank_text():
    assert chunk_text("") == []
    assert chunk_text("   \n\t  ", size=10, overlap=2) == []


def test_chunk_text_keeps_short_text_whole():
    assert chunk_text("short text") == ["short text"]


def test_chunk_text_overlaps_and_covers_all_text():
    text = "".join(str(i % 10) for i in range(2500))

    chunks = chunk_text(text, size=800, overlap=100)

    assert all(chunk.strip() for chunk in chunks)
    assert all(len(chunk) <= 800 for chunk in chunks)
    for index, chunk in enumerate(chunks):
        assert text[index * 700 : index * 700 + len(chunk)] == chunk
    assert chunks[-1] == text[-len(chunks[-1]) :]
    assert chunks[0][-100:] == chunks[1][:100]


def test_chunk_text_rejects_invalid_parameters():
    with pytest.raises(ValueError):
        chunk_text("text", size=10, overlap=10)
