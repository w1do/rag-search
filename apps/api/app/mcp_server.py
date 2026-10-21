import contextlib
from collections.abc import AsyncIterator, Callable, Iterator
from typing import Any
from uuid import UUID

from fastapi import FastAPI
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from sqlalchemy.orm import Session as DbSession
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.deps import (
    Services,
    build_request_services,
    get_blob_store,
    get_embedder,
    get_engine,
    get_extractor,
    get_indexes,
    get_services,
    get_settings,
)
from vector_core.domain import DomainError, Principal, PrincipalKind, SearchHit
from vector_core.security import safe_equals
from vector_core.services.common import TransactionHooks

MCP_PATH = "/mcp"
UNAUTHORIZED_BODY = {"detail": {"code": "unauthorized", "message": "authentication required"}}
AGENT = Principal(PrincipalKind.AGENT)


@contextlib.contextmanager
def services_scope(app: FastAPI) -> Iterator[Services]:
    override: Callable[[], Services] | None = app.dependency_overrides.get(get_services)
    if override is not None:
        yield override()
        return
    settings = _resolve(app, get_settings)()
    session = DbSession(_resolve(app, get_engine)())
    hooks = TransactionHooks()
    try:
        vector_index, text_index = get_indexes()
        yield build_request_services(
            session,
            settings,
            get_blob_store(settings),
            get_extractor(),
            hooks,
            vector_index,
            text_index,
            get_embedder(),
        )
        session.commit()
        hooks.commit()
    except BaseException:
        try:
            session.rollback()
        finally:
            hooks.rollback()
        raise
    finally:
        session.close()


@contextlib.contextmanager
def _tool_errors() -> Iterator[None]:
    try:
        yield
    except DomainError as exc:
        raise ToolError(f"{exc.code}: {exc}") from exc


def _resolve(app: FastAPI, dependency: Callable[..., Any]) -> Callable[..., Any]:
    return app.dependency_overrides.get(dependency, dependency)


def _project_id(value: str) -> UUID:
    try:
        return UUID(value)
    except ValueError as exc:
        raise ToolError("validation_error: project_id must be a UUID") from exc


def _check_range(name: str, value: int, low: int, high: int) -> None:
    if not low <= value <= high:
        raise ToolError(f"validation_error: {name} must be between {low} and {high}")


def _hit_payload(hit: SearchHit) -> dict[str, Any]:
    return {
        "source_id": str(hit.source_id),
        "title": hit.title,
        "version": hit.version,
        "text": hit.text,
        "score": hit.score,
        "locator": hit.locator,
    }


def create_mcp_server(app: FastAPI) -> MCPServer:
    server = MCPServer(name="vector")

    @server.tool(name="search", description="Hybrid search over a project's published sources.")
    def search(project_id: str, query: str, limit: int = 10) -> dict[str, Any]:
        pid = _project_id(project_id)
        _check_range("limit", limit, 1, 50)
        with _tool_errors(), services_scope(app) as services:
            services.auth.authorize(pid, AGENT)
            result = services.search.search(pid, query, limit)
        return {"hits": [_hit_payload(hit) for hit in result.hits], "warning": result.warning}

    @server.tool(
        name="context",
        description="Build a bounded context block with sources for a question in a project.",
    )
    def context(project_id: str, query: str, max_chars: int = 4000) -> dict[str, Any]:
        pid = _project_id(project_id)
        _check_range("max_chars", max_chars, 200, 50000)
        with _tool_errors(), services_scope(app) as services:
            services.auth.authorize(pid, AGENT)
            result = services.search.context(pid, query, max_chars)
        return {
            "context": result.context,
            "sources": [
                {"source_id": str(s.source_id), "title": s.title, "version": s.version}
                for s in result.sources
            ],
            "warning": result.warning,
        }

    @server.tool(name="add_note", description="Add a text note as a source of a project.")
    def add_note(project_id: str, title: str, text: str) -> dict[str, Any]:
        pid = _project_id(project_id)
        with _tool_errors(), services_scope(app) as services:
            services.auth.authorize(pid, AGENT)
            source = services.sources.add_text(pid, title, text)
        return {"source_id": str(source.id), "title": source.title, "status": source.status.value}

    return server


def _bearer_token(scope: Scope) -> str | None:
    request = Request(scope)
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    return token


class McpEndpoint:
    """ASGI endpoint for MCP over streamable HTTP, guarded by the agent token."""

    def __init__(self, app: FastAPI) -> None:
        self._app = app
        self._server = create_mcp_server(app)
        self._inner: ASGIApp | None = None

    @contextlib.asynccontextmanager
    async def run(self) -> AsyncIterator[None]:
        inner = self._server.streamable_http_app(
            streamable_http_path=MCP_PATH,
            json_response=True,
            stateless_http=True,
            transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
        )
        async with inner.router.lifespan_context(inner):
            self._inner = inner
            try:
                yield
            finally:
                self._inner = None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return
        token = _bearer_token(scope)
        expected = _resolve(self._app, get_settings)().mcp_token
        if token is None or not expected or not safe_equals(token, expected):
            response = JSONResponse(
                UNAUTHORIZED_BODY, status_code=401, headers={"WWW-Authenticate": "Bearer"}
            )
            await response(scope, receive, send)
            return
        if self._inner is None:
            response = JSONResponse(
                {"detail": {"code": "mcp_unavailable", "message": "MCP is not running"}},
                status_code=503,
            )
            await response(scope, receive, send)
            return
        await self._inner(scope, receive, send)
