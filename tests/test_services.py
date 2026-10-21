from datetime import timedelta
from uuid import uuid4

import pytest

from tests.conftest import MCP_TOKEN, OWNER_EMAIL, OWNER_PASSWORD
from tests.fakes import InMemoryBlobStore, InMemoryStore, build_services
from vector_core.config import Settings
from vector_core.domain import (
    AuthNotConfigured,
    Conflict,
    Forbidden,
    NotFound,
    Principal,
    PrincipalKind,
    Session,
    Unauthorized,
    ValidationError,
    utcnow,
)
from vector_core.security import hash_token


def test_create_project_strips_name(services):
    project = services.projects.create("  Alpha  ")

    assert project.name == "Alpha"
    assert services.projects.get(project.id) == project


def test_create_project_rejects_blank_name(services):
    with pytest.raises(ValidationError):
        services.projects.create("   ")


def test_create_project_rejects_duplicate_name(services):
    services.projects.create("Alpha")

    with pytest.raises(Conflict):
        services.projects.create(" Alpha ")


def test_rename_keeps_id_and_rejects_name_used_by_another_project(services):
    first = services.projects.create("First")
    services.projects.create("Second")

    renamed = services.projects.rename(first.id, "Renamed")
    assert renamed.id == first.id
    assert services.projects.get(first.id).name == "Renamed"

    with pytest.raises(Conflict):
        services.projects.rename(first.id, "Second")
    assert services.projects.get(first.id).name == "Renamed"


def test_issued_secret_is_returned_once_and_only_its_hash_is_stored(services, store):
    project = services.projects.create("Alpha")

    issued = services.keys.issue(project.id)

    assert issued.secret.startswith(f"vk_{issued.prefix}_")
    (stored,) = store.keys.values()
    assert stored.key_hash == hash_token(issued.secret)
    assert all(issued.secret not in str(value) for value in vars(stored).values())


def test_api_key_authenticates_its_project_until_revoked(services):
    project = services.projects.create("Alpha")
    issued = services.keys.issue(project.id)

    assert services.keys.authenticate(issued.secret) == project.id

    services.keys.revoke(project.id, issued.id)
    with pytest.raises(Unauthorized):
        services.keys.authenticate(issued.secret)


def test_unknown_api_key_is_unauthorized(services):
    with pytest.raises(Unauthorized):
        services.keys.authenticate("vk_00000000_unknown")


def test_revoke_key_requires_key_of_the_same_project(services):
    alpha = services.projects.create("Alpha")
    beta = services.projects.create("Beta")
    issued = services.keys.issue(alpha.id)

    with pytest.raises(NotFound):
        services.keys.revoke(beta.id, issued.id)


def test_deleting_project_revokes_access_of_its_keys(services):
    project = services.projects.create("Alpha")
    issued = services.keys.issue(project.id)

    services.projects.delete(project.id)

    with pytest.raises(Unauthorized):
        services.keys.authenticate(issued.secret)


def test_deleting_project_removes_its_blobs_in_fake_store(settings, store):
    blobs = InMemoryBlobStore()
    services = build_services(settings, store, blobs=blobs)
    project = services.projects.create("Alpha")
    source = services.sources.add_text(project.id, "Notes", "hello")
    version = services.sources.versions(project.id, source.id)[0]

    services.projects.delete(project.id)

    assert not blobs.exists(version.storage_key)


def test_api_key_principal_is_limited_to_its_project(services):
    alpha = services.projects.create("Alpha")
    beta = services.projects.create("Beta")
    principal = Principal(PrincipalKind.API_KEY, alpha.id)

    services.auth.authorize(alpha.id, principal)
    with pytest.raises(Forbidden):
        services.auth.authorize(beta.id, principal)


def test_login_issues_session_that_logout_revokes(services):
    token, _ = services.auth.login(OWNER_EMAIL, OWNER_PASSWORD)

    assert services.auth.authenticate_session(token) is not None

    services.auth.logout(token)
    with pytest.raises(Unauthorized):
        services.auth.authenticate_session(token)


def test_login_purges_only_expired_sessions(services, store):
    now = utcnow()
    expired = Session(
        id=uuid4(),
        token_hash="expired-hash",
        created_at=now - timedelta(days=2),
        expires_at=now - timedelta(days=1),
    )
    live = Session(
        id=uuid4(),
        token_hash="live-hash",
        created_at=now,
        expires_at=now + timedelta(days=1),
    )
    store.sessions[expired.token_hash] = expired
    store.sessions[live.token_hash] = live

    token, _ = services.auth.login(OWNER_EMAIL, OWNER_PASSWORD)

    assert expired.token_hash not in store.sessions
    assert live.token_hash in store.sessions
    assert hash_token(token) in store.sessions


@pytest.mark.parametrize(
    ("email", "password"),
    [("wrong@example.test", OWNER_PASSWORD), (OWNER_EMAIL, "wrong-password")],
)
def test_login_rejects_wrong_credentials(services, email, password):
    with pytest.raises(Unauthorized):
        services.auth.login(email, password)


def test_login_requires_configured_credentials(store):
    unconfigured = Settings(database_url="", email="", password="", mcp_token=MCP_TOKEN)
    services = build_services(unconfigured, store)

    with pytest.raises(AuthNotConfigured):
        services.auth.login("", "")


def test_agent_principal_accepts_only_configured_mcp_token(services):
    principal = services.auth.agent_principal(MCP_TOKEN)

    assert principal == Principal(PrincipalKind.AGENT)
    assert services.auth.agent_principal("other-token") is None


def test_agent_principal_is_disabled_without_mcp_token(store):
    settings = Settings(database_url="", email=OWNER_EMAIL, password=OWNER_PASSWORD, mcp_token="")
    services = build_services(settings, InMemoryStore())

    assert services.auth.agent_principal("") is None
    assert services.auth.agent_principal(uuid4().hex) is None
