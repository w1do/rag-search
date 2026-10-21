from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Query, Response, UploadFile
from pydantic import BaseModel

from app.deps import Services, get_services, require_project_access
from app.routers.sources import JobOut, SourceOut, read_bounded
from vector_core.domain import SyncFile, SyncFileStatus

router = APIRouter(
    prefix="/api/v1/projects/{project_id}",
    tags=["sync"],
    dependencies=[Depends(require_project_access)],
)


class SyncFileIn(BaseModel):
    origin_key: str
    content_hash: str
    size: int


class SyncManifestIn(BaseModel):
    sync_root: str
    files: list[SyncFileIn]


class SyncPlanOut(BaseModel):
    to_upload: list[str]
    unchanged: list[str]
    to_prune: list[UUID]


class SyncFileOut(BaseModel):
    source: SourceOut
    origin_key: str
    status: SyncFileStatus


@router.post("/sources/sync", response_model=SyncPlanOut)
def sync_plan(
    project_id: UUID, body: SyncManifestIn, services: Services = Depends(get_services)
) -> SyncPlanOut:
    plan = services.sources.sync_plan(
        project_id,
        body.sync_root,
        [SyncFile(f.origin_key, f.content_hash, f.size) for f in body.files],
    )
    return SyncPlanOut(
        to_upload=plan.to_upload, unchanged=plan.unchanged, to_prune=plan.to_prune
    )


@router.post("/sources/sync/file", status_code=201, response_model=SyncFileOut)
def sync_file(
    project_id: UUID,
    response: Response,
    file: UploadFile = File(...),
    origin_key: str = Form(...),
    sync_root: str = Form(...),
    services: Services = Depends(get_services),
) -> SyncFileOut:
    data = read_bounded(file.file, services.sources.max_upload_bytes)
    source, status = services.sources.sync_file(
        project_id, sync_root, origin_key, data, file.content_type
    )
    if status is not SyncFileStatus.CREATED:
        response.status_code = 200
    return SyncFileOut(
        source=SourceOut.model_validate(source), origin_key=origin_key, status=status
    )


@router.delete("/sources/{source_id}", status_code=204, response_class=Response)
def delete_synced_source(
    project_id: UUID,
    source_id: UUID,
    sync_root: str = Query(...),
    services: Services = Depends(get_services),
) -> Response:
    services.sources.delete_synced(project_id, sync_root, source_id)
    return Response(status_code=204)


@router.post("/reindex", status_code=202, response_model=JobOut)
def reindex_project(
    project_id: UUID, services: Services = Depends(get_services)
) -> JobOut:
    return JobOut.model_validate(services.sources.reindex_project(project_id))
