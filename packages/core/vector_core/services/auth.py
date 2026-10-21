from datetime import datetime, timedelta
from uuid import UUID, uuid4

from vector_core.config import Settings
from vector_core.domain import (
    AuthNotConfigured,
    Forbidden,
    Principal,
    PrincipalKind,
    Session,
    Unauthorized,
    utcnow,
)
from vector_core.repositories import SessionRepository
from vector_core.security import generate_session_token, hash_token, safe_equals
from vector_core.services.common import INVALID_CREDENTIALS


class AuthService:
    def __init__(self, settings: Settings, sessions: SessionRepository) -> None:
        self._settings = settings
        self._sessions = sessions

    def login(self, email: str, password: str) -> tuple[str, datetime]:
        if not self._settings.auth_configured:
            raise AuthNotConfigured("EMAIL and PASSWORD must be configured")
        email_ok = safe_equals(email, self._settings.email)
        password_ok = safe_equals(password, self._settings.password)
        if not (email_ok and password_ok):
            raise Unauthorized(INVALID_CREDENTIALS)
        self.purge_expired()
        token = generate_session_token()
        created_at = utcnow()
        expires_at = created_at + timedelta(seconds=self._settings.session_ttl_seconds)
        self._sessions.add(
            Session(
                id=uuid4(),
                token_hash=hash_token(token),
                created_at=created_at,
                expires_at=expires_at,
            )
        )
        return token, expires_at

    def logout(self, token: str) -> None:
        self._sessions.delete_by_hash(hash_token(token))

    def purge_expired(self) -> int:
        return self._sessions.delete_expired(utcnow())

    def authenticate_session(self, token: str) -> Session:
        session = self._sessions.get_by_hash(hash_token(token))
        if session is None or session.expires_at <= utcnow():
            raise Unauthorized(INVALID_CREDENTIALS)
        return session

    def agent_principal(self, token: str) -> Principal | None:
        if not self._settings.mcp_token or not safe_equals(
            token, self._settings.mcp_token
        ):
            return None
        return Principal(PrincipalKind.AGENT)

    def authorize(self, project_id: UUID, principal: Principal) -> None:
        if (
            principal.kind is PrincipalKind.API_KEY
            and principal.project_id != project_id
        ):
            raise Forbidden("api key does not grant access to this project")
