import io

import httpx
import pytest
from docx import Document
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
from vector_core.chunking import chunk_text
from vector_core.domain import PayloadTooLarge, UnsupportedFormat, ValidationError
from vector_core.extract import SUPPORTED_EXTENSIONS, extract_text, fetch_url


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


def test_html_skips_script_and_style_and_collapses_whitespace():
    data = (
        b"<html><head><style>p{}</style><script>var x=1;</script></head>"
        b"<body><p>Hello   <b>wide</b>\n world</p></body></html>"
    )
    assert extract_text(data, "text/html; charset=utf-8", None) == "Hello wide world"


def test_plain_text_content_type_is_decoded_without_filename():
    assert extract_text(b"plain", "text/plain", None) == "plain"


def test_unknown_format_is_unsupported():
    with pytest.raises(UnsupportedFormat):
        extract_text(b"\x89PNG", "image/png", "picture.png")


def test_fetch_url_rejects_non_http_schemes():
    with pytest.raises(ValidationError):
        fetch_url("ftp://example.test/file.txt", timeout=5, max_bytes=100)


def test_fetch_url_returns_body_and_content_type():
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            content=b"hello",
            headers={"content-type": "text/plain; charset=utf-8"},
        )
    )

    body, content_type = fetch_url(
        "https://example.test/a.txt", timeout=5, max_bytes=100, transport=transport
    )

    assert body == b"hello"
    assert content_type == "text/plain; charset=utf-8"


def test_fetch_url_rejects_declared_size_over_limit():
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=b"x" * 11))

    with pytest.raises(PayloadTooLarge):
        fetch_url("https://example.test/big", timeout=5, max_bytes=10, transport=transport)


def test_fetch_url_rejects_streamed_body_over_limit():
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=iter([b"x" * 6, b"x" * 6])))

    with pytest.raises(PayloadTooLarge):
        fetch_url("https://example.test/stream", timeout=5, max_bytes=10, transport=transport)


def test_fetch_url_maps_http_errors_to_validation_error():
    transport = httpx.MockTransport(lambda request: httpx.Response(500))

    with pytest.raises(ValidationError):
        fetch_url("https://example.test/broken", timeout=5, max_bytes=100, transport=transport)


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
