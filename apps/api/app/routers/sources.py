from datetime import datetime
from typing import BinaryIO
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Response, UploadFile
from pydantic import BaseModel, ConfigDict

from app.deps import Services, get_services, require_owner
from vector_core.domain import PayloadTooLarge, ValidationError

router = APIRouter(
    prefix="/api/projects/{project_id}/sources",
    tags=["sources"],
    dependencies=[Depends(require_owner)],
)

READ_CHUNK_BYTES = 1024 * 1024


class TextIn(BaseModel):
    title: str
    text: str


class UrlIn(BaseModel):
    url: str
    title: str | None = None


class SourcePatch(BaseModel):
    title: str | None = None
    text: str | None = None


class SourceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    kind: str
    title: str
    status: str
    current_version: int | None
    origin_key: str | None
    content_type: str | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime


class VersionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    version: int
    status: str
    content_hash: str | None
    extracted_chars: int | None
    vector_indexed: bool
    error_message: str | None
    created_at: datetime


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    type: str
    status: str


def read_bounded(stream: BinaryIO, limit: int) -> bytes:
    body = bytearray()
    while chunk := stream.read(READ_CHUNK_BYTES):
        body.extend(chunk)
        if len(body) > limit:
            raise PayloadTooLarge(f"payload exceeds {limit} bytes")
    return bytes(body)


@router.post("/text", status_code=201, response_model=SourceOut)
def add_text(
    project_id: UUID, body: TextIn, services: Services = Depends(get_services)
) -> SourceOut:
    return SourceOut.model_validate(
        services.sources.add_text(project_id, body.title, body.text)
    )


@router.post("/file", status_code=201, response_model=SourceOut)
def add_file(
    project_id: UUID,
    file: UploadFile = File(...),
    title: str | None = Form(default=None),
    services: Services = Depends(get_services),
) -> SourceOut:
    """Sync endpoint: reading, text extraction and SQL run in the threadpool, not on the event loop."""
    if not file.filename:
        raise ValidationError("filename is required")
    data = read_bounded(file.file, services.sources.max_upload_bytes)
    source = services.sources.add_file(
        project_id, file.filename, data, file.content_type, title
    )
    return SourceOut.model_validate(source)


@router.post("/url", status_code=201, response_model=SourceOut)
def add_url(
    project_id: UUID, body: UrlIn, services: Services = Depends(get_services)
) -> SourceOut:
    return SourceOut.model_validate(
        services.sources.add_url(project_id, body.url, body.title)
    )


@router.get("", response_model=list[SourceOut])
def list_sources(
    project_id: UUID, services: Services = Depends(get_services)
) -> list[SourceOut]:
    return [
        SourceOut.model_validate(s)
        for s in services.sources.list_for_project(project_id)
    ]


@router.get("/{source_id}", response_model=SourceOut)
def get_source(
    project_id: UUID, source_id: UUID, services: Services = Depends(get_services)
) -> SourceOut:
    return SourceOut.model_validate(services.sources.get(project_id, source_id))


@router.patch("/{source_id}", response_model=SourceOut)
def edit_source(
    project_id: UUID,
    source_id: UUID,
    body: SourcePatch,
    services: Services = Depends(get_services),
) -> SourceOut:
    return SourceOut.model_validate(
        services.sources.edit(project_id, source_id, body.title, body.text)
    )


@router.delete("/{source_id}", status_code=204, response_class=Response)
def delete_source(
    project_id: UUID, source_id: UUID, services: Services = Depends(get_services)
) -> Response:
    services.sources.delete(project_id, source_id)
    return Response(status_code=204)


@router.get("/{source_id}/versions", response_model=list[VersionOut])
def list_versions(
    project_id: UUID, source_id: UUID, services: Services = Depends(get_services)
) -> list[VersionOut]:
    return [
        VersionOut.model_validate(v)
        for v in services.sources.versions(project_id, source_id)
    ]


@router.post("/{source_id}/reindex", status_code=202, response_model=JobOut)
def reindex_source(
    project_id: UUID, source_id: UUID, services: Services = Depends(get_services)
) -> JobOut:
    return JobOut.model_validate(services.sources.reindex(project_id, source_id))
