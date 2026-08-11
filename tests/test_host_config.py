import json
import os
import stat
from pathlib import Path

import pytest
from pydantic import ValidationError

import harness.host_config as host_config_module
from harness import CredentialStore, HostConfig, HostConfigStore, load_env_file


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
    assert payload == {
        "schema_version": 1,
        "brave_api_key": "brave-secret",
        "operator_password_hash": None,
    }


def test_credentials_are_written_one_at_a_time_without_dropping_the_other(
    tmp_path: Path,
) -> None:
    store = CredentialStore(tmp_path / "credentials.json")

    store.write_brave_api_key("brave-secret")
    store.write_operator_password_hash("pbkdf2_sha256$1$00$11")

    assert store.read("brave_api_key") == "brave-secret"
    assert store.read_operator_password_hash() == "pbkdf2_sha256$1$00$11"

    store.write_brave_api_key("rotated-secret")

    assert store.read("brave_api_key") == "rotated-secret"
    assert store.read_operator_password_hash() == "pbkdf2_sha256$1$00$11"
    assert CredentialStore(tmp_path / "missing.json").read_operator_password_hash() is None


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


@pytest.fixture
def environ(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Um os.environ descartável: load_env_file grava de verdade no processo."""
    replacement = dict(os.environ)
    monkeypatch.setattr(os, "environ", replacement)
    return replacement


def test_an_exported_variable_wins_over_the_env_file(
    tmp_path: Path, environ: dict[str, str]
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("HARNESS_PORT=8899\nHARNESS_OLLAMA_URL=http://file\n", encoding="utf-8")
    environ["HARNESS_PORT"] = "9001"
    environ.pop("HARNESS_OLLAMA_URL", None)

    load_env_file(env_file)

    assert environ["HARNESS_PORT"] == "9001"
    assert environ["HARNESS_OLLAMA_URL"] == "http://file"


def test_env_file_keeps_quoted_values_and_paths_with_spaces(
    tmp_path: Path, environ: dict[str, str]
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        'HARNESS_WORKSPACE_ROOTS="/home/a b/c"\nHARNESS_STATE_DIR=/home/a b/state\n',
        encoding="utf-8",
    )
    environ.pop("HARNESS_WORKSPACE_ROOTS", None)
    environ.pop("HARNESS_STATE_DIR", None)

    load_env_file(env_file)

    assert environ["HARNESS_WORKSPACE_ROOTS"] == "/home/a b/c"
    assert environ["HARNESS_STATE_DIR"] == "/home/a b/state"


def test_env_file_skips_comments_blank_lines_and_export(
    tmp_path: Path, environ: dict[str, str]
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("# comentário\n\nexport HARNESS_PORT=9100\n", encoding="utf-8")
    environ.pop("HARNESS_PORT", None)

    load_env_file(env_file)

    assert environ["HARNESS_PORT"] == "9100"


def test_a_missing_env_file_is_not_an_error(tmp_path: Path, environ: dict[str, str]) -> None:
    before = dict(environ)

    assert load_env_file(tmp_path / "nao-existe") is None
    assert environ == before


def test_a_malformed_env_line_is_refused_naming_the_line(
    tmp_path: Path, environ: dict[str, str]
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("HARNESS_PORT=9100\nHARNESS_HOST 127.0.0.1\n", encoding="utf-8")
    before = dict(environ)

    with pytest.raises(ValueError, match="linha 2"):
        load_env_file(env_file)
    # Recusa é total: nenhuma linha do arquivo entra antes do erro.
    assert environ == before


def test_a_secret_in_a_world_readable_env_file_is_refused(
    tmp_path: Path, environ: dict[str, str]
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("HARNESS_BRAVE_API_KEY=brave-secret\n", encoding="utf-8")
    env_file.chmod(0o644)
    environ.pop("HARNESS_BRAVE_API_KEY", None)

    with pytest.raises(ValueError, match="chmod 600"):
        load_env_file(env_file)
    assert "HARNESS_BRAVE_API_KEY" not in environ

    env_file.chmod(0o600)
    load_env_file(env_file)

    assert environ["HARNESS_BRAVE_API_KEY"] == "brave-secret"
