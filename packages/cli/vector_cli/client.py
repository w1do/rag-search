from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Any, Self
from uuid import UUID

import httpx

DEFAULT_TIMEOUT_SECONDS = 60.0
API_KEY_HEADER = "X-Api-Key"


class VectorApiError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(f"{status} {code}: {message}")
        self.status = status
        self.code = code
        self.message = message


@dataclass(frozen=True)
class SyncManifestEntry:
    origin_key: str
    content_hash: str
    size: int


@dataclass(frozen=True)
class SyncPlanResult:
    to_upload: list[str]
    unchanged: list[str]
    to_prune: list[str]


class VectorClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        http: httpx.Client | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._api_key = api_key
        self._owns_http = http is None
        self._http = http or httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout)

    def close(self) -> None:
        if self._owns_http:
            self._http.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def sync_plan(
        self, project_id: UUID, sync_root: str, files: list[SyncManifestEntry]
    ) -> SyncPlanResult:
        body = {
            "sync_root": sync_root,
            "files": [
                {"origin_key": f.origin_key, "content_hash": f.content_hash, "size": f.size}
                for f in files
            ],
        }
        data = self._request(
            "POST", f"/api/v1/projects/{project_id}/sources/sync", json=body
        )
        return SyncPlanResult(
            to_upload=list(data["to_upload"]),
            unchanged=list(data["unchanged"]),
            to_prune=list(data["to_prune"]),
        )

    def upload(
        self,
        project_id: UUID,
        sync_root: str,
        origin_key: str,
        path: Path,
    ) -> dict[str, Any]:
        with path.open("rb") as stream:
            return self._request(
                "POST",
                f"/api/v1/projects/{project_id}/sources/sync/file",
                data={"origin_key": origin_key, "sync_root": sync_root},
                files={"file": (path.name, stream)},
            )

    def delete_synced(self, project_id: UUID, sync_root: str, source_id: str) -> None:
        self._request(
            "DELETE",
            f"/api/v1/projects/{project_id}/sources/{source_id}",
            params={"sync_root": sync_root},
            expect_json=False,
        )

    def reindex(self, project_id: UUID) -> dict[str, Any]:
        return self._request("POST", f"/api/v1/projects/{project_id}/reindex")

    def search(self, project_id: UUID, query: str, limit: int) -> dict[str, Any]:
        return self._request(
            "POST",
            "/api/v1/search",
            json={"project_id": str(project_id), "query": query, "limit": limit},
        )

    def _request(
        self,
        method: str,
        path: str,
        *,
        expect_json: bool = True,
        **kwargs: Any,
    ) -> Any:
        try:
            response = self._http.request(
                method, path, headers={API_KEY_HEADER: self._api_key}, **kwargs
            )
        except httpx.HTTPError as exc:
            raise VectorApiError(0, "transport_error", str(exc)) from exc
        if response.status_code >= 400:
            raise _api_error(response)
        if not expect_json or response.status_code == 204:
            return None
        return response.json()


def _api_error(response: httpx.Response) -> VectorApiError:
    code = "http_error"
    message = response.text or response.reason_phrase
    try:
        detail = response.json().get("detail")
    except ValueError:
        detail = None
    if isinstance(detail, dict):
        code = str(detail.get("code", code))
        message = str(detail.get("message", message))
    return VectorApiError(response.status_code, code, message)
