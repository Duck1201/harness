"""Operator authentication and session lifetime.

Network exposure is derived from the credential, not from a flag: with no Operator
password configured the server only answers direct loopback connections, and with
one configured every API route needs a session. There is no configuration that
opens the port to a network without authentication (ADR-0005).
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

_ALGORITHM = "pbkdf2_sha256"
_ITERATIONS = 600_000
_SALT_BYTES = 16
_TOKEN_BYTES = 32
_MINIMUM_PASSWORD_LENGTH = 12


class AuthenticationError(Exception):
    def __init__(self, code: str, message: str, *, status_code: int) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


def hash_password(password: str, *, iterations: int = _ITERATIONS) -> str:
    """Derives a storable hash. The password itself is never written anywhere."""
    normalized = _normalize(password)
    salt = secrets.token_bytes(_SALT_BYTES)
    derived = hashlib.pbkdf2_hmac("sha256", normalized.encode("utf-8"), salt, iterations)
    return f"{_ALGORITHM}${iterations}${salt.hex()}${derived.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, raw_iterations, salt_hex, digest_hex = encoded.split("$")
        if algorithm != _ALGORITHM:
            return False
        iterations = int(raw_iterations)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
    except (ValueError, AttributeError):
        return False
    derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(derived, expected)


def _normalize(password: str) -> str:
    if len(password) < _MINIMUM_PASSWORD_LENGTH:
        raise ValueError(
            f"the Operator password must have at least {_MINIMUM_PASSWORD_LENGTH} characters"
        )
    if "\n" in password or "\r" in password:
        raise ValueError("the Operator password must be a single line")
    return password


@dataclass(frozen=True, slots=True)
class SessionStatus:
    authentication_required: bool
    authenticated: bool
    expires_at: datetime | None


class SessionController:
    """Issues and validates opaque session tokens, kept only in memory.

    A restart drops every session on purpose: nothing about who was logged in is
    worth persisting, and a session file would be one more secret at rest.
    """

    def __init__(
        self,
        *,
        password_hash: str | None,
        ttl_seconds: float = 12 * 60 * 60,
        now: Callable[[], datetime] | None = None,
        max_sessions: int = 32,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("session TTL must be positive")
        if max_sessions < 1:
            raise ValueError("max_sessions must be positive")
        self._password_hash = password_hash
        self._ttl = timedelta(seconds=ttl_seconds)
        self._now = now or _utc_now
        self._max_sessions = max_sessions
        self._sessions: dict[str, datetime] = {}

    @property
    def authentication_required(self) -> bool:
        return self._password_hash is not None

    def status(self, token: str | None) -> SessionStatus:
        expires_at = self._expiry(token) if token is not None else None
        return SessionStatus(
            authentication_required=self.authentication_required,
            authenticated=expires_at is not None,
            expires_at=expires_at,
        )

    def login(self, password: str) -> tuple[str, datetime]:
        if self._password_hash is None:
            raise AuthenticationError(
                "authentication_not_configured",
                "This host has no Operator password.",
                status_code=409,
            )
        if not verify_password(password, self._password_hash):
            raise AuthenticationError(
                "invalid_credentials",
                "The Operator password is incorrect.",
                status_code=401,
            )
        self._expire()
        if len(self._sessions) >= self._max_sessions:
            oldest = min(self._sessions, key=lambda key: self._sessions[key])
            self._sessions.pop(oldest, None)
        token = secrets.token_urlsafe(_TOKEN_BYTES)
        expires_at = self._now() + self._ttl
        self._sessions[token] = expires_at
        return token, expires_at

    def authorize(self, token: str | None) -> None:
        if not self.authentication_required:
            return
        if token is None or self._expiry(token) is None:
            raise AuthenticationError(
                "authentication_required",
                "A valid Operator session is required.",
                status_code=401,
            )

    def logout(self, token: str | None) -> None:
        if token is not None:
            self._sessions.pop(token, None)

    def set_password_hash(self, password_hash: str | None) -> None:
        """Rotating or clearing the credential invalidates every open session."""
        self._password_hash = password_hash
        self._sessions.clear()

    def _expiry(self, token: str) -> datetime | None:
        self._expire()
        # Constant-time lookup: comparing the supplied token against each known one
        # keeps a wrong guess from being distinguishable by timing.
        for known, expires_at in self._sessions.items():
            if hmac.compare_digest(known, token):
                return expires_at
        return None

    def _expire(self) -> None:
        current = self._now()
        for token in [token for token, expiry in self._sessions.items() if expiry <= current]:
            self._sessions.pop(token, None)


def _utc_now() -> datetime:
    return datetime.now(UTC)
