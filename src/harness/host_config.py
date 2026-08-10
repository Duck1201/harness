import json
import os
import stat
from collections.abc import Mapping
from contextlib import suppress
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, SecretStr, field_validator


class HostConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    allowed_workspace_roots: tuple[Path, ...]
    tokenizer_path: Path
    tokenizer_digest: str
    state_dir: Path
    allowed_origins: tuple[str, ...]
    brave_credential_ref: Literal["brave_api_key"] | None = None

    @field_validator("allowed_workspace_roots")
    @classmethod
    def validate_workspace_roots(cls, roots: tuple[Path, ...]) -> tuple[Path, ...]:
        validated: list[Path] = []
        for root in roots:
            canonical = _canonical_path(root, must_exist=True)
            if not canonical.is_dir():
                raise ValueError("allowed workspace roots must be directories")
            if canonical not in validated:
                validated.append(canonical)
        return tuple(validated)

    @field_validator("tokenizer_path", "state_dir")
    @classmethod
    def validate_absolute_path(cls, path: Path) -> Path:
        return _canonical_path(path, must_exist=False)

    @field_validator("tokenizer_digest")
    @classmethod
    def validate_tokenizer_digest(cls, value: str) -> str:
        digest = value.removeprefix("sha256:").lower()
        if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
            raise ValueError("tokenizer digest must be a SHA-256 digest")
        return digest

    @field_validator("allowed_origins")
    @classmethod
    def validate_allowed_origins(cls, origins: tuple[str, ...]) -> tuple[str, ...]:
        validated = tuple(dict.fromkeys(origins))
        if any(not _valid_origin(origin) for origin in validated):
            raise ValueError("allowed origins must be HTTP or HTTPS origins")
        return validated


class HostConfigStore:
    def __init__(
        self,
        path: str | Path | None = None,
        *,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        selected = Path(path) if path is not None else default_host_config_path(environ)
        self.path = selected.expanduser().resolve(strict=False)

    @property
    def credentials_path(self) -> Path:
        return self.path.with_name("credentials.json")

    def exists(self) -> bool:
        return self.path.is_file()

    def load(self) -> HostConfig:
        return HostConfig.model_validate_json(_read_private_file(self.path))

    def load_optional(self) -> HostConfig | None:
        try:
            return self.load()
        except FileNotFoundError:
            return None

    def write(self, config: HostConfig) -> None:
        payload = config.model_dump(mode="json")
        _atomic_write_json(self.path, payload)


class _CredentialFile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    brave_api_key: SecretStr


class CredentialStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve(strict=False)

    def write_brave_api_key(self, value: str) -> None:
        normalized = _normalize_secret(value)
        _atomic_write_json(
            self.path,
            {"schema_version": 1, "brave_api_key": normalized},
        )

    def read(self, reference: str) -> str:
        if reference != "brave_api_key":
            raise ValueError("unknown credential reference")
        credentials = _CredentialFile.model_validate_json(_read_private_file(self.path))
        return credentials.brave_api_key.get_secret_value()


def default_host_config_path(environ: Mapping[str, str] | None = None) -> Path:
    environment = os.environ if environ is None else environ
    configured = environment.get("XDG_CONFIG_HOME")
    base = Path(configured).expanduser() if configured else Path.home() / ".config"
    if not base.is_absolute():
        raise ValueError("XDG_CONFIG_HOME must be an absolute path")
    return (base / "harness-2" / "host.json").resolve(strict=False)


def _canonical_path(path: Path, *, must_exist: bool) -> Path:
    if not path.is_absolute():
        raise ValueError("host paths must be absolute")
    try:
        canonical = path.resolve(strict=must_exist)
    except OSError as error:
        raise ValueError("host path could not be resolved") from error
    if path != canonical:
        raise ValueError("host paths must be canonical")
    return canonical


def _normalize_secret(value: str) -> str:
    normalized = value.strip()
    if not normalized or "\n" in normalized or "\r" in normalized:
        raise ValueError("credential must be a non-empty single line")
    return normalized


def _valid_origin(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme in {"http", "https"}
        and parsed.hostname is not None
        and parsed.username is None
        and parsed.password is None
        and parsed.path == ""
        and parsed.query == ""
        and parsed.fragment == ""
        and (port is None or 0 < port < 65536)
    )


def _read_private_file(path: Path) -> bytes:
    descriptor: int | None = None
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) != 0o600:
            raise OSError("configuration file must be private and regular")
        chunks: list[bytes] = []
        while chunk := os.read(descriptor, 65536):
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _atomic_write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    descriptor: int | None = None
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC,
            0o600,
        )
        content = (
            json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode("utf-8")
        written = 0
        while written < len(content):
            written += os.write(descriptor, content[written:])
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        with suppress(FileNotFoundError):
            temporary.unlink()
