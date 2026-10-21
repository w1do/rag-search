from pathlib import Path

import pytest
from vector_core.blobs import FsBlobStore
from vector_core.domain import NotFound, ValidationError


@pytest.fixture
def blobs(tmp_path: Path) -> FsBlobStore:
    return FsBlobStore(tmp_path / "blobs")


def test_put_creates_directories_and_get_returns_bytes(blobs):
    blobs.put("projects/p1/source.txt", b"data")

    assert blobs.exists("projects/p1/source.txt")
    assert blobs.get("projects/p1/source.txt") == b"data"


def test_get_missing_blob_is_not_found(blobs):
    with pytest.raises(NotFound):
        blobs.get("missing.txt")


def test_delete_removes_single_blob(blobs):
    blobs.put("a.txt", b"a")

    blobs.delete("a.txt")
    blobs.delete("a.txt")

    assert not blobs.exists("a.txt")


def test_delete_prefix_removes_directory_only(blobs):
    blobs.put("sources/s1/v1.txt", b"1")
    blobs.put("sources/s1/v2.txt", b"2")
    blobs.put("sources/s2/v1.txt", b"3")

    blobs.delete_prefix("sources/s1")

    assert not blobs.exists("sources/s1/v1.txt")
    assert not blobs.exists("sources/s1/v2.txt")
    assert blobs.get("sources/s2/v1.txt") == b"3"


@pytest.mark.parametrize("key", ["../escape.txt", "a/../../escape.txt", "/absolute.txt", "", "a\\b.txt"])
def test_traversal_and_invalid_keys_are_rejected(blobs, key):
    with pytest.raises(ValidationError):
        blobs.put(key, b"x")


def test_root_cannot_be_deleted_by_prefix(blobs):
    with pytest.raises(ValidationError):
        blobs.delete_prefix("")
