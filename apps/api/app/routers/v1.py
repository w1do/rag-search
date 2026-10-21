from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.deps import Services, get_services, require_principal, require_project_access
from app.routers.projects import ProjectOut
from vector_core.domain import ContextResult, Principal, SearchResult

router = APIRouter(prefix="/api/v1", tags=["v1"])

QUERY_MAX_LENGTH = 2000


class SearchIn(BaseModel):
    project_id: UUID
    query: str = Field(min_length=1, max_length=QUERY_MAX_LENGTH)
    limit: int = Field(default=10, ge=1, le=50)


class ContextIn(BaseModel):
    project_id: UUID
    query: str = Field(min_length=1, max_length=QUERY_MAX_LENGTH)
    max_chars: int = Field(default=4000, ge=200, le=50000)


class SearchHitOut(BaseModel):
    source_id: UUID
    title: str
    version: int
    text: str
    score: float
    locator: dict[str, object]


class SearchOut(BaseModel):
    hits: list[SearchHitOut]
    warning: str | None = None


class ContextSourceOut(BaseModel):
    source_id: UUID
    title: str
    version: int


class ContextOut(BaseModel):
    context: str
    sources: list[ContextSourceOut]
    warning: str | None = None


@router.get(
    "/projects/{project_id}",
    response_model=ProjectOut,
    dependencies=[Depends(require_project_access)],
)
def get_project_v1(project_id: UUID, services: Services = Depends(get_services)) -> ProjectOut:
    return ProjectOut.model_validate(services.projects.get(project_id))


@router.post("/search", response_model=SearchOut)
def search_v1(
    body: SearchIn,
    principal: Principal = Depends(require_principal),
    services: Services = Depends(get_services),
) -> SearchOut:
    services.auth.authorize(body.project_id, principal)
    result: SearchResult = services.search.search(body.project_id, body.query, body.limit)
    return SearchOut(
        hits=[SearchHitOut.model_validate(hit, from_attributes=True) for hit in result.hits],
        warning=result.warning,
    )


@router.post("/context", response_model=ContextOut)
def context_v1(
    body: ContextIn,
    principal: Principal = Depends(require_principal),
    services: Services = Depends(get_services),
) -> ContextOut:
    services.auth.authorize(body.project_id, principal)
    result: ContextResult = services.search.context(body.project_id, body.query, body.max_chars)
    return ContextOut(
        context=result.context,
        sources=[
            ContextSourceOut.model_validate(source, from_attributes=True) for source in result.sources
        ],
        warning=result.warning,
    )
