import shutil
from pathlib import Path

from vector_core.domain import NotFound, ValidationError


class FsBlobStore:
    def __init__(self, root: Path) -> None:
        self._root = root.resolve()

    def put(self, key: str, data: bytes) -> None:
        path = self._resolve(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def get(self, key: str) -> bytes:
        path = self._resolve(key)
        if not path.is_file():
            raise NotFound("blob not found")
        return path.read_bytes()

    def delete(self, key: str) -> None:
        self._resolve(key).unlink(missing_ok=True)

    def delete_prefix(self, prefix: str) -> None:
        path = self._resolve(prefix)
        if path.is_dir():
            shutil.rmtree(path)
        elif path.is_file():
            path.unlink()

    def exists(self, key: str) -> bool:
        return self._resolve(key).is_file()

    def _resolve(self, key: str) -> Path:
        if not key or key.startswith("/") or "\\" in key:
            raise ValidationError("invalid blob key")
        path = (self._root / key).resolve()
        if path == self._root or not path.is_relative_to(self._root):
            raise ValidationError("invalid blob key")
        return path
