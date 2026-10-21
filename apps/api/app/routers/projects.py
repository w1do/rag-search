from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, ConfigDict

from app.deps import Services, get_services, require_owner

router = APIRouter(prefix="/api/projects", tags=["projects"], dependencies=[Depends(require_owner)])


class ProjectIn(BaseModel):
    name: str


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    created_at: datetime


@router.post("", status_code=201, response_model=ProjectOut)
def create_project(body: ProjectIn, services: Services = Depends(get_services)) -> ProjectOut:
    return ProjectOut.model_validate(services.projects.create(body.name))


@router.get("", response_model=list[ProjectOut])
def list_projects(services: Services = Depends(get_services)) -> list[ProjectOut]:
    return [ProjectOut.model_validate(project) for project in services.projects.list_all()]


@router.get("/{project_id}", response_model=ProjectOut)
def get_project(project_id: UUID, services: Services = Depends(get_services)) -> ProjectOut:
    return ProjectOut.model_validate(services.projects.get(project_id))


@router.patch("/{project_id}", response_model=ProjectOut)
def rename_project(project_id: UUID, body: ProjectIn, services: Services = Depends(get_services)) -> ProjectOut:
    return ProjectOut.model_validate(services.projects.rename(project_id, body.name))


@router.delete("/{project_id}", status_code=204)
def delete_project(project_id: UUID, services: Services = Depends(get_services)) -> Response:
    services.projects.delete(project_id)
    return Response(status_code=204)
