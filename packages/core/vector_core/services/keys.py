from dataclasses import replace
from uuid import UUID, uuid4

from vector_core.domain import ApiKey, ApiKeyIssued, NotFound, Unauthorized, utcnow
from vector_core.repositories import ApiKeyRepository, ProjectRepository
from vector_core.security import generate_api_key, hash_token
from vector_core.services.common import INVALID_CREDENTIALS


class ApiKeyService:
    def __init__(self, keys: ApiKeyRepository, projects: ProjectRepository) -> None:
        self._keys = keys
        self._projects = projects

    def issue(self, project_id: UUID) -> ApiKeyIssued:
        self._require_project(project_id)
        secret, prefix = generate_api_key()
        key = ApiKey(
            id=uuid4(),
            project_id=project_id,
            prefix=prefix,
            key_hash=hash_token(secret),
            created_at=utcnow(),
        )
        self._keys.add(key)
        return ApiKeyIssued(
            id=key.id,
            project_id=project_id,
            prefix=prefix,
            secret=secret,
            created_at=key.created_at,
        )

    def list_for_project(self, project_id: UUID) -> list[ApiKey]:
        self._require_project(project_id)
        return self._keys.list_by_project(project_id)

    def revoke(self, project_id: UUID, key_id: UUID) -> ApiKey:
        self._require_project(project_id)
        key = self._keys.get(key_id)
        if key is None or key.project_id != project_id:
            raise NotFound("api key not found")
        if key.revoked_at is not None:
            return key
        revoked_at = utcnow()
        self._keys.set_revoked_at(key_id, revoked_at)
        return replace(key, revoked_at=revoked_at)

    def authenticate(self, raw_key: str) -> UUID:
        key = self._keys.get_by_hash(hash_token(raw_key))
        if key is None or key.revoked_at is not None:
            raise Unauthorized(INVALID_CREDENTIALS)
        return key.project_id

    def _require_project(self, project_id: UUID) -> None:
        if self._projects.get(project_id) is None:
            raise NotFound("project not found")
