import json
import stat
from pathlib import Path

import pytest
from pydantic import ValidationError

import harness.host_config as host_config_module
from harness import CredentialStore, HostConfig, HostConfigStore


def test_host_config_round_trips_as_private_versioned_json(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    tokenizer = tmp_path / "tokenizer.json"
    tokenizer.write_text("{}", encoding="utf-8")
    state_dir = tmp_path / "state"
    store = HostConfigStore(tmp_path / "config" / "host.json")
    config = HostConfig(
        schema_version=1,
        allowed_workspace_roots=(workspace.resolve(),),
        tokenizer_path=tokenizer.resolve(),
        tokenizer_digest="0" * 64,
        state_dir=state_dir.resolve(),
        allowed_origins=("http://127.0.0.1:8000",),
        brave_credential_ref="brave_api_key",
    )

    result = store.write(config)

    assert result is None
    assert store.load() == config
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600
    assert json.loads(store.path.read_text(encoding="utf-8"))["schema_version"] == 1


def test_credential_store_keeps_brave_secret_separate_and_private(tmp_path: Path) -> None:
    store = CredentialStore(tmp_path / "credentials.json")

    result = store.write_brave_api_key("brave-secret")

    assert result is None
    assert store.read("brave_api_key") == "brave-secret"
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600
    payload = json.loads(store.path.read_text(encoding="utf-8"))
    assert payload == {"schema_version": 1, "brave_api_key": "brave-secret"}


def test_host_config_replace_is_atomic_when_the_final_swap_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    tokenizer = tmp_path / "tokenizer.json"
    tokenizer.write_text("{}", encoding="utf-8")
    store = HostConfigStore(tmp_path / "config" / "host.json")
    original = HostConfig(
        allowed_workspace_roots=(workspace.resolve(),),
        tokenizer_path=tokenizer.resolve(),
        tokenizer_digest="1" * 64,
        state_dir=(tmp_path / "state-one").resolve(),
        allowed_origins=("http://operator.test",),
    )
    store.write(original)
    updated = original.model_copy(update={"state_dir": (tmp_path / "state-two").resolve()})

    def fail_replace(source: Path, destination: Path) -> None:
        del source, destination
        raise OSError("simulated interrupted swap")

    monkeypatch.setattr(host_config_module.os, "replace", fail_replace)
    with pytest.raises(OSError, match="interrupted swap"):
        store.write(updated)

    assert store.load() == original
    assert list(store.path.parent.glob(".*.tmp")) == []


def test_default_host_path_uses_xdg_config_home(tmp_path: Path) -> None:
    store = HostConfigStore(environ={"XDG_CONFIG_HOME": str(tmp_path)})

    assert store.path == (tmp_path / "harness-2" / "host.json").resolve()


def test_host_config_rejects_nonexistent_roots_relative_paths_and_invalid_origins(
    tmp_path: Path,
) -> None:
    tokenizer = tmp_path / "tokenizer.json"
    tokenizer.write_text("{}", encoding="utf-8")

    with pytest.raises(ValidationError, match="could not be resolved"):
        HostConfig(
            allowed_workspace_roots=(tmp_path / "missing",),
            tokenizer_path=tokenizer.resolve(),
            tokenizer_digest="2" * 64,
            state_dir=(tmp_path / "state").resolve(),
            allowed_origins=("http://operator.test",),
        )
    with pytest.raises(ValidationError, match="absolute"):
        HostConfig(
            allowed_workspace_roots=(tmp_path.resolve(),),
            tokenizer_path=tokenizer.resolve(),
            tokenizer_digest="2" * 64,
            state_dir=Path("relative"),
            allowed_origins=("http://operator.test",),
        )
    with pytest.raises(ValidationError, match="origins"):
        HostConfig(
            allowed_workspace_roots=(tmp_path.resolve(),),
            tokenizer_path=tokenizer.resolve(),
            tokenizer_digest="2" * 64,
            state_dir=(tmp_path / "state").resolve(),
            allowed_origins=("http://operator.test/path",),
        )
