from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

from app.deps import Services, get_services, require_owner

router = APIRouter(
    prefix="/api/projects/{project_id}/keys",
    tags=["keys"],
    dependencies=[Depends(require_owner)],
)


class KeyIssuedOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    prefix: str
    secret: str
    created_at: datetime


class KeyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    prefix: str
    status: str
    created_at: datetime
    revoked_at: datetime | None


@router.post("", status_code=201, response_model=KeyIssuedOut)
def issue_key(project_id: UUID, services: Services = Depends(get_services)) -> KeyIssuedOut:
    return KeyIssuedOut.model_validate(services.keys.issue(project_id))


@router.get("", response_model=list[KeyOut])
def list_keys(project_id: UUID, services: Services = Depends(get_services)) -> list[KeyOut]:
    return [KeyOut.model_validate(key) for key in services.keys.list_for_project(project_id)]


@router.post("/{key_id}/revoke", response_model=KeyOut)
def revoke_key(project_id: UUID, key_id: UUID, services: Services = Depends(get_services)) -> KeyOut:
    return KeyOut.model_validate(services.keys.revoke(project_id, key_id))
