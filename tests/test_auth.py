from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from harness import (
    ApplicationService,
    ConversationStore,
    EngineReadiness,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ObservabilityStore,
    ToolSchema,
    create_app,
    load_config,
)
from harness.auth import (
    AuthenticationError,
    SessionController,
    hash_password,
    verify_password,
)

# With no Operator password configured the server only answers direct loopback,
# so a test has to say where its request comes from.
LOOPBACK = ("127.0.0.1", 51000)


class FakeEstimator:
    validated = True
    readiness = EngineReadiness(ready=True)

    def estimate(self, messages: Sequence[ModelMessage], tools: Sequence[ToolSchema]) -> int:
        return len(messages) + len(tools)


class FakeRuntime:
    async def verify_profile(self) -> EngineReadiness:
        return EngineReadiness(ready=True)

    async def generate(self, request: ModelRequest) -> ModelResponse:
        del request
        return ModelResponse(content="ok")

    async def aclose(self) -> None:
        return None


PASSWORD = "operator-password-1"
REMOTE = ("192.168.1.50", 51000)


def _app(tmp_path: Path, sessions: SessionController):
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    service = ApplicationService(
        store=ConversationStore(tmp_path / "conversations.sqlite3"),
        observability_store=ObservabilityStore(tmp_path / "observability.sqlite3"),
        config=load_config(),
        runtime=FakeRuntime(),
        estimator=FakeEstimator(),
        allowed_workspace_roots=(workspace,),
    )
    return create_app(
        service=service,
        static_dir=tmp_path / "missing-dist",
        session_controller=sessions,
    )


def test_password_hashing_never_stores_the_password_and_rejects_weak_ones() -> None:
    encoded = hash_password(PASSWORD, iterations=1_000)

    assert PASSWORD not in encoded
    assert encoded.startswith("pbkdf2_sha256$1000$")
    assert verify_password(PASSWORD, encoded)
    assert not verify_password("operator-password-2", encoded)
    # A malformed record must not authenticate anything.
    assert not verify_password(PASSWORD, "garbage")
    with pytest.raises(ValueError, match="at least"):
        hash_password("short")
    with pytest.raises(ValueError, match="single line"):
        hash_password("password-with\nnewline")


def test_sessions_expire_and_a_password_change_invalidates_every_open_one() -> None:
    clock = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)
    controller = SessionController(
        password_hash=hash_password(PASSWORD, iterations=1_000),
        ttl_seconds=60,
        now=lambda: clock,
    )

    token, expires_at = controller.login(PASSWORD)
    controller.authorize(token)
    assert expires_at == clock + timedelta(seconds=60)
    assert controller.status(token).authenticated is True

    with pytest.raises(AuthenticationError) as wrong:
        controller.login("operator-password-2")
    assert wrong.value.code == "invalid_credentials"
    with pytest.raises(AuthenticationError):
        controller.authorize("invented-token")

    clock += timedelta(seconds=61)
    with pytest.raises(AuthenticationError) as expired:
        controller.authorize(token)
    assert expired.value.code == "authentication_required"

    fresh, _ = controller.login(PASSWORD)
    controller.set_password_hash(hash_password("operator-password-3", iterations=1_000))
    with pytest.raises(AuthenticationError):
        controller.authorize(fresh)


def test_without_a_password_only_loopback_is_served(tmp_path: Path) -> None:
    app = _app(tmp_path, SessionController(password_hash=None))

    with TestClient(app, client=LOOPBACK) as local:
        local_workspaces = local.get("/api/workspaces")
        local_session = local.get("/api/session").json()
    with TestClient(app, client=REMOTE) as remote:
        remote_workspaces = remote.get("/api/workspaces")
        remote_health = remote.get("/api/health")
        forwarded = remote.get("/api/workspaces", headers={"X-Forwarded-For": "127.0.0.1"})

    assert local_workspaces.status_code == 200
    assert local_session["authentication_required"] is False
    assert remote_workspaces.status_code == 401
    assert remote_workspaces.json()["error"]["code"] == "authentication_required"
    # Health has to answer a supervisor before any login exists.
    assert remote_health.status_code == 200
    # A proxy cannot claim to be the local machine.
    assert forwarded.status_code == 401


def test_with_a_password_every_api_route_needs_a_session(tmp_path: Path) -> None:
    sessions = SessionController(password_hash=hash_password(PASSWORD, iterations=1_000))
    app = _app(tmp_path, sessions)

    with TestClient(app, client=REMOTE) as client:
        anonymous = client.get("/api/workspaces")
        wrong = client.post("/api/session", json={"password": "operator-password-2"})
        login = client.post("/api/session", json={"password": PASSWORD})
        token = login.json()["session"]
        authorized = client.get("/api/workspaces", headers={"X-Harness-Session": token})
        status = client.get("/api/session", headers={"X-Harness-Session": token}).json()
        logout = client.delete("/api/session", headers={"X-Harness-Session": token})
        after_logout = client.get("/api/workspaces", headers={"X-Harness-Session": token})

    assert anonymous.status_code == 401
    assert anonymous.json()["error"]["code"] == "authentication_required"
    assert wrong.status_code == 401
    assert wrong.json()["error"]["code"] == "invalid_credentials"
    assert login.status_code == 201
    # The token is opaque and carries nothing about the password.
    assert PASSWORD not in login.text
    assert authorized.status_code == 200
    assert status == {
        "authentication_required": True,
        "authenticated": True,
        "expires_at": login.json()["expires_at"],
    }
    assert logout.status_code == 204
    assert after_logout.status_code == 401


def test_the_setup_token_cannot_be_used_as_a_session(tmp_path: Path) -> None:
    sessions = SessionController(password_hash=hash_password(PASSWORD, iterations=1_000))
    app = _app(tmp_path, sessions)

    with TestClient(app, client=LOOPBACK) as client:
        setup_status = client.get("/api/setup/status")
        borrowed = client.get(
            "/api/workspaces",
            headers={"X-Harness-Session": "setup-token-value"},
        )
        wrong_header = client.get(
            "/api/workspaces",
            headers={"X-Harness-Setup-Token": "setup-token-value"},
        )

    # Setup status stays reachable; it is guarded by its own ephemeral token.
    assert setup_status.status_code == 200
    assert borrowed.status_code == 401
    assert wrong_header.status_code == 401


def test_readiness_of_the_service_is_unaffected_by_authentication(tmp_path: Path) -> None:
    sessions = SessionController(password_hash=None)
    app = _app(tmp_path, sessions)

    with TestClient(app, client=LOOPBACK) as client:
        health = client.get("/api/health").json()

    assert health["ready"] is True
    assert health["capabilities"]["authentication_required"] is False
    assert EngineReadiness(ready=True).ready is True
