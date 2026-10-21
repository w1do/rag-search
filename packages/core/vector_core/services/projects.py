import logging
from dataclasses import replace
from uuid import UUID, uuid4

from vector_core.domain import Conflict, NotFound, Project, ServiceUnavailable, utcnow
from vector_core.ports import BlobStore, IndexBackendError, TextIndex, VectorIndex
from vector_core.repositories import ProjectRepository
from vector_core.services.common import TransactionHooks, clean_name

logger = logging.getLogger(__name__)


class ProjectService:
    def __init__(
        self,
        projects: ProjectRepository,
        blobs: BlobStore,
        hooks: TransactionHooks,
        text_index: TextIndex,
        vector_index: VectorIndex,
    ) -> None:
        self._projects = projects
        self._blobs = blobs
        self._hooks = hooks
        self._text_index = text_index
        self._vector_index = vector_index

    def create(self, name: str) -> Project:
        cleaned = clean_name(name)
        if self._projects.get_by_name(cleaned) is not None:
            raise Conflict("project name already used")
        project = Project(id=uuid4(), name=cleaned, created_at=utcnow())
        self._projects.add(project)
        return project

    def list_all(self) -> list[Project]:
        return self._projects.list_all()

    def get(self, project_id: UUID) -> Project:
        project = self._projects.get(project_id)
        if project is None:
            raise NotFound("project not found")
        return project

    def rename(self, project_id: UUID, name: str) -> Project:
        cleaned = clean_name(name)
        current = self.get(project_id)
        other = self._projects.get_by_name(cleaned)
        if other is not None and other.id != project_id:
            raise Conflict("project name already used")
        self._projects.rename(project_id, cleaned)
        return replace(current, name=cleaned)

    def delete(self, project_id: UUID) -> None:
        self.get(project_id)
        self._purge_indexes(project_id)
        if not self._projects.delete(project_id):
            raise NotFound("project not found")
        self._hooks.after_commit(lambda: self._blobs.delete_prefix(str(project_id)))

    def _purge_indexes(self, project_id: UUID) -> None:
        for name, index in (("text", self._text_index), ("vector", self._vector_index)):
            try:
                index.delete_by_project(project_id)
            except IndexBackendError as exc:
                logger.warning("%s index purge for project %s failed: %s", name, project_id, exc)
                raise ServiceUnavailable(
                    "search index unavailable, project was not deleted", code="index_unavailable"
                ) from exc
