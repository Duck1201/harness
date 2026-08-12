import asyncio
import hashlib
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from tokenizers import Tokenizer
from tokenizers.models import WordLevel

import harness.api as api_module
from harness import (
    ApplicationService,
    ConversationStore,
    CredentialStore,
    EngineReadiness,
    HostConfigStore,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ObservabilityStore,
    SetupController,
    SetupSubmission,
    ToolSchema,
    create_app,
    load_config,
)
from harness.host_config import default_state_dir


class FakeEstimator:
    validated = True
    readiness = EngineReadiness(ready=True)

    def estimate(self, messages: Sequence[ModelMessage], tools: Sequence[ToolSchema]) -> int:
        return len(messages) + len(tools)


class FakeRuntime:
    def __init__(self, **kwargs: object) -> None:
        del kwargs

    async def verify_profile(self) -> EngineReadiness:
        return EngineReadiness(ready=True)

    async def generate(self, request: ModelRequest) -> ModelResponse:
        del request
        return ModelResponse(content="ok")

    async def aclose(self) -> None:
        return None


def _service(tmp_path: Path) -> ApplicationService:
    return ApplicationService(
        store=ConversationStore(tmp_path / "conversations.sqlite3"),
        observability_store=ObservabilityStore(tmp_path / "observability.sqlite3"),
        config=load_config(),
        runtime=FakeRuntime(),
        estimator=FakeEstimator(),
        allowed_workspace_roots=(),
    )


def _tokenizer(path: Path) -> str:
    tokenizer = Tokenizer(WordLevel({"[UNK]": 0}, unk_token="[UNK]"))
    tokenizer.save(str(path))  # pyright: ignore[reportUnknownMemberType]
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _payload(tmp_path: Path) -> dict[str, object]:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    tokenizer_path = tmp_path / "tokenizer.json"
    _tokenizer(tokenizer_path)
    return {
        "allowed_workspace_roots": [str(workspace.resolve())],
        "tokenizer_path": str(tokenizer_path.resolve()),
        "state_dir": str((tmp_path / "state").resolve()),
        "allowed_origins": ["http://operator.test"],
        "searxng_url": "https://searx.example/search",
    }


def test_loopback_setup_persists_host_and_secret_then_requires_restart(
    tmp_path: Path,
) -> None:
    payload = _payload(tmp_path)
    workspace = tmp_path / "workspace"
    host_store = HostConfigStore(tmp_path / "config" / "host.json")
    credential_store = CredentialStore(host_store.credentials_path)
    controller = SetupController(
        host_store,
        credential_store=credential_store,
        token="setup-token-secret",
        ttl_seconds=300,
        now=lambda: datetime(2026, 8, 10, 12, 0, tzinfo=UTC),
    )
    app = create_app(
        service=_service(tmp_path),
        setup_controller=controller,
        allowed_origins=("http://operator.test",),
        static_dir=tmp_path / "missing-dist",
    )
    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        before = client.get("/api/setup/status")
        response = client.post(
            "/api/setup",
            json=payload,
            headers={
                "Origin": "http://operator.test",
                "X-Harness-Setup-Token": "setup-token-secret",
            },
        )
        after = client.get("/api/setup/status")

    state_dir = default_state_dir()
    suggested = {
        "suggested_state_dir": str(state_dir),
        "suggested_tokenizer_path": str(state_dir / "tokenizer.json"),
    }
    assert before.json() == {
        "configured": False,
        "required": True,
        "restart_required": False,
        "token_expires_at": "2026-08-10T12:05:00Z",
        **suggested,
    }
    assert response.status_code == 200
    assert response.json() == {"restart_required": True}
    assert "setup-token-secret" not in response.text
    assert "searx.example" in host_store.path.read_text(encoding="utf-8")
    assert after.json() == {
        "configured": True,
        "required": False,
        "restart_required": True,
        "token_expires_at": None,
        **suggested,
    }
    host = host_store.load()
    assert host.allowed_workspace_roots == (workspace.resolve(),)
    assert (
        host.tokenizer_digest
        == hashlib.sha256(Path(str(payload["tokenizer_path"])).read_bytes()).hexdigest()
    )
    assert host.searxng_url == "https://searx.example/search"


def test_setup_token_is_rejected_when_wrong_and_consumed_only_after_success(
    tmp_path: Path,
) -> None:
    payload = _payload(tmp_path)
    host_store = HostConfigStore(tmp_path / "config" / "host.json")
    controller = SetupController(host_store, token="one-use-token")
    app = create_app(
        service=_service(tmp_path),
        setup_controller=controller,
        allowed_origins=("http://operator.test",),
        static_dir=tmp_path / "missing-dist",
    )
    headers = {
        "Origin": "http://operator.test",
        "X-Harness-Setup-Token": "wrong-token",
    }

    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        wrong = client.post("/api/setup", json=payload, headers=headers)
        headers["X-Harness-Setup-Token"] = "one-use-token"
        accepted = client.post("/api/setup", json=payload, headers=headers)
        replay = client.post("/api/setup", json=payload, headers=headers)

    assert wrong.status_code == 403
    assert wrong.json()["error"]["code"] == "invalid_setup_token"
    assert accepted.status_code == 200
    assert replay.status_code == 409
    assert replay.json()["error"]["code"] == "setup_not_available"


def test_setup_authorizes_before_parsing_and_redacts_invalid_payloads(
    tmp_path: Path,
) -> None:
    payload = _payload(tmp_path)
    payload["state_dir"] = "relative-state"
    host_store = HostConfigStore(tmp_path / "config" / "host.json")
    service = _service(tmp_path)
    controller = SetupController(host_store, token="validation-token")
    app = create_app(
        service=service,
        setup_controller=controller,
        allowed_origins=("http://operator.test",),
        static_dir=tmp_path / "missing-dist",
    )
    origin = {"Origin": "http://operator.test"}

    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        unauthorized = client.post(
            "/api/setup",
            content=b"not-json",
            headers={**origin, "X-Harness-Setup-Token": "wrong-token"},
        )
        invalid = client.post(
            "/api/setup",
            json=payload,
            headers={**origin, "X-Harness-Setup-Token": "validation-token"},
        )

    assert unauthorized.json()["error"]["code"] == "invalid_setup_token"
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "invalid_setup_configuration"
    assert "validation-token" not in invalid.text
    assert host_store.exists() is False
    assert asyncio.run(service.observability_store.list_events()) == []


def test_setup_rejects_a_search_endpoint_that_is_not_a_url(tmp_path: Path) -> None:
    payload = _payload(tmp_path)
    payload["searxng_url"] = "not-a-url"
    host_store = HostConfigStore(tmp_path / "config" / "host.json")
    controller = SetupController(host_store, token="credential-token")
    app = create_app(
        service=_service(tmp_path),
        setup_controller=controller,
        allowed_origins=("http://operator.test",),
        static_dir=tmp_path / "missing-dist",
    )

    with TestClient(
        app,
        client=("127.0.0.1", 50000),
        raise_server_exceptions=False,
    ) as client:
        response = client.post(
            "/api/setup",
            json=payload,
            headers={
                "Origin": "http://operator.test",
                "X-Harness-Setup-Token": "credential-token",
            },
        )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_setup_configuration"
    assert "credential-token" not in response.text
    assert host_store.exists() is False


def test_setup_validates_workspace_and_tokenizer_before_writing(
    tmp_path: Path,
) -> None:
    payload = _payload(tmp_path)
    host_store = HostConfigStore(tmp_path / "config" / "host.json")
    controller = SetupController(host_store, token="validation-token")
    app = create_app(
        service=_service(tmp_path),
        setup_controller=controller,
        allowed_origins=("http://operator.test",),
        static_dir=tmp_path / "missing-dist",
    )
    headers = {
        "Origin": "http://operator.test",
        "X-Harness-Setup-Token": "validation-token",
    }

    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        missing_root_payload = {**payload, "allowed_workspace_roots": [str(tmp_path / "missing")]}
        missing_root = client.post("/api/setup", json=missing_root_payload, headers=headers)
        unexpected_field = client.post(
            "/api/setup",
            json={**payload, "tokenizer_digest": "0" * 64},
            headers=headers,
        )
        invalid_tokenizer_path = tmp_path / "invalid-tokenizer.json"
        invalid_tokenizer_path.write_text("{}", encoding="utf-8")
        invalid_tokenizer = client.post(
            "/api/setup",
            json={**payload, "tokenizer_path": str(invalid_tokenizer_path.resolve())},
            headers=headers,
        )

    assert missing_root.status_code == 422
    assert unexpected_field.json()["error"]["code"] == "invalid_setup_request"
    assert invalid_tokenizer.json()["error"]["code"] == "invalid_tokenizer"
    assert host_store.exists() is False


def test_expired_setup_token_is_rejected_without_writing_config(tmp_path: Path) -> None:
    payload = _payload(tmp_path)
    now = [datetime(2026, 8, 10, 12, 0, tzinfo=UTC)]
    host_store = HostConfigStore(tmp_path / "config" / "host.json")
    controller = SetupController(
        host_store,
        token="expired-token",
        ttl_seconds=60,
        now=lambda: now[0],
    )
    app = create_app(
        service=_service(tmp_path),
        setup_controller=controller,
        allowed_origins=("http://operator.test",),
        static_dir=tmp_path / "missing-dist",
    )
    now[0] = datetime(2026, 8, 10, 12, 1, tzinfo=UTC)

    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        response = client.post(
            "/api/setup",
            json=payload,
            headers={
                "Origin": "http://operator.test",
                "X-Harness-Setup-Token": "expired-token",
            },
        )

    assert response.status_code == 410
    assert response.json()["error"]["code"] == "setup_token_expired"
    assert host_store.exists() is False


def test_setup_rejects_lan_and_forwarded_requests_before_reading_secrets(
    tmp_path: Path,
) -> None:
    payload = _payload(tmp_path)
    host_store = HostConfigStore(tmp_path / "config" / "host.json")
    service = _service(tmp_path)
    controller = SetupController(host_store, token="loopback-token")
    app = create_app(
        service=service,
        setup_controller=controller,
        allowed_origins=("http://operator.test",),
        static_dir=tmp_path / "missing-dist",
    )
    headers = {
        "Origin": "http://operator.test",
        "X-Harness-Setup-Token": "loopback-token",
    }

    with TestClient(app, client=("192.168.1.20", 50000)) as lan_client:
        lan = lan_client.post("/api/setup", json=payload, headers=headers)
    with TestClient(app, client=("127.0.0.1", 50000)) as local_client:
        forwarded = local_client.post(
            "/api/setup",
            json=payload,
            headers={**headers, "Forwarded": "for=127.0.0.1"},
        )
        forwarded_port = local_client.post(
            "/api/setup",
            json=payload,
            headers={**headers, "X-Forwarded-Port": "8000"},
        )

    assert lan.status_code == 403
    assert forwarded.status_code == 403
    assert forwarded_port.status_code == 403
    assert lan.json()["error"]["code"] == "direct_loopback_required"
    assert forwarded.json()["error"]["code"] == "direct_loopback_required"
    assert forwarded_port.json()["error"]["code"] == "direct_loopback_required"
    assert "loopback-token" not in lan.text
    assert host_store.exists() is False
    assert asyncio.run(service.observability_store.list_events()) == []


def test_default_boot_without_host_config_is_degraded_and_does_not_create_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    host_path = tmp_path / "config" / "host.json"
    # Sem host.json o state dir cai no padrão XDG; apontá-lo para o tmp mantém o
    # teste longe do estado real da máquina.
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setattr(api_module, "OllamaRuntime", FakeRuntime)

    app = create_app(
        host_config_path=host_path,
        static_dir=tmp_path / "missing-dist",
        setup_token="boot-token",
    )

    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        status = client.get("/api/setup/status").json()
        health = client.get("/api/health").json()
        workspaces = client.get("/api/workspaces").json()
        settings = client.get("/api/ui/settings").json()

    assert status["configured"] is False
    assert status["required"] is True
    assert health["ready"] is False
    assert health["reason_code"] == "tokenizer_file_missing"
    assert workspaces == {"workspaces": []}
    assert settings["setup_required"] is True
    assert settings["restart_required"] is False
    assert host_path.exists() is False


def test_default_boot_reads_host_config_and_host_origins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = _payload(tmp_path)
    host_store = HostConfigStore(tmp_path / "config" / "host.json")
    controller = SetupController(host_store, token="initial-setup")
    controller.complete("initial-setup", SetupSubmission.model_validate(payload))
    monkeypatch.setattr(api_module, "OllamaRuntime", FakeRuntime)

    app = create_app(host_config_path=host_store.path, static_dir=tmp_path / "missing-dist")

    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        status = client.get("/api/setup/status", headers={"Origin": "http://operator.test"})
        health = client.get("/api/health").json()
        workspaces = client.get("/api/workspaces").json()["workspaces"]
        settings = client.get("/api/ui/settings").json()

    assert status.status_code == 200
    assert status.json()["configured"] is True
    assert status.json()["required"] is False
    assert health["ready"] is True
    assert workspaces == [
        {"id": workspaces[0]["id"], "root": str((tmp_path / "workspace").resolve())}
    ]
    assert settings["setup_required"] is False
    assert settings["restart_required"] is False


def test_settings_tab_rewrites_host_config_and_asks_for_a_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = _payload(tmp_path)
    host_store = HostConfigStore(tmp_path / "config" / "host.json")
    SetupController(host_store, token="initial-setup").complete(
        "initial-setup", SetupSubmission.model_validate(payload)
    )
    other_workspace = tmp_path / "other-workspace"
    other_workspace.mkdir()
    monkeypatch.setattr(api_module, "OllamaRuntime", FakeRuntime)

    app = create_app(host_config_path=host_store.path, static_dir=tmp_path / "missing-dist")

    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        before = client.get("/api/ui/settings").json()
        saved = client.put(
            "/api/admin/host-config",
            json={
                **payload,
                "allowed_workspace_roots": [str(other_workspace.resolve())],
                "searxng_url": "http://127.0.0.1:8080/search",
                "ollama_url": "http://127.0.0.1:11500",
            },
        )
        invalid = client.put(
            "/api/admin/host-config",
            json={**payload, "tokenizer_path": str(tmp_path / "missing-tokenizer.json")},
        )
        after = client.get("/api/ui/settings").json()

    assert before["mutable"] is True
    assert before["host_config"]["allowed_workspace_roots"] == [
        str((tmp_path / "workspace").resolve())
    ]
    assert saved.status_code == 200
    assert saved.json() == {"restart_required": True}
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "invalid_tokenizer"
    # O arquivo já mudou; o processo em pé continua com o que leu no boot.
    stored = host_store.load()
    assert stored.allowed_workspace_roots == (other_workspace.resolve(),)
    assert stored.searxng_url == "http://127.0.0.1:8080/search"
    assert stored.ollama_url == "http://127.0.0.1:11500"
    assert after["host_config"]["ollama_url"] == "http://127.0.0.1:11500"


def test_setup_can_be_explicitly_reopened_and_randomizes_each_boot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = _payload(tmp_path)
    host_store = HostConfigStore(tmp_path / "config" / "host.json")
    initial = SetupController(host_store, token="initial-setup")
    initial.complete("initial-setup", SetupSubmission.model_validate(payload))
    monkeypatch.setattr(api_module, "OllamaRuntime", FakeRuntime)

    app = create_app(
        host_config_path=host_store.path,
        reopen_setup=True,
        static_dir=tmp_path / "missing-dist",
        setup_token="reopened-token",
    )
    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        status = client.get("/api/setup/status").json()

    first_boot = SetupController(HostConfigStore(tmp_path / "fresh" / "host.json"))
    second_boot = SetupController(HostConfigStore(tmp_path / "fresh" / "host.json"))

    assert status["configured"] is True
    assert status["required"] is True
    assert first_boot.boot_console_message() != second_boot.boot_console_message()
    assert "token=" in (first_boot.boot_console_message() or "")
