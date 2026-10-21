import contextlib
import logging
from collections.abc import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import OperationalError

from app.mcp_server import McpEndpoint
from app.routers import auth, keys, projects, sources, sync, v1
from vector_core.config import Settings
from vector_core.domain import DomainError

logger = logging.getLogger(__name__)


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    async with mcp_endpoint.run():
        yield


swagger_enabled = Settings.from_env().swagger_enabled
app = FastAPI(
    title="Vector API",
    lifespan=lifespan,
    docs_url="/docs" if swagger_enabled else None,
    redoc_url="/redoc" if swagger_enabled else None,
    openapi_url="/openapi.json" if swagger_enabled else None,
)
mcp_endpoint = McpEndpoint(app)

app.include_router(auth.router)
app.include_router(projects.router)
app.include_router(keys.router)
app.include_router(sources.router)
app.include_router(sync.router)
app.include_router(v1.router)
app.add_route("/mcp", mcp_endpoint, include_in_schema=False)


@app.exception_handler(DomainError)
def handle_domain_error(request: Request, exc: DomainError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": {"code": exc.code, "message": str(exc)}},
    )


@app.exception_handler(OperationalError)
def handle_database_unavailable(request: Request, exc: OperationalError) -> JSONResponse:
    logger.warning("database unavailable: %s", type(exc.orig).__name__)
    return JSONResponse(
        status_code=503,
        content={"detail": {"code": "db_unavailable", "message": "database unavailable"}},
    )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
