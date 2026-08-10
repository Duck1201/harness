from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


class PageVerificationMode(StrEnum):
    DISABLED = "disabled"
    AUTOMATIC_ONCE_PER_PAGE_REVISION = "automatic_once_per_page_revision"


class PageVerificationExecutionStatus(StrEnum):
    DISABLED = "disabled"
    EXECUTED = "executed"
    CACHED = "cached"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SKIPPED_UNCHANGED = "skipped_unchanged"


class PageStatus(StrEnum):
    VALID = "valid"
    INVALID = "invalid"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class PageRevisionKey:
    workspace_id: str
    relative_path: str
    html_sha256: str
    workspace_revision: int
    verifier_digest: str

    @property
    def digest(self) -> str:
        canonical = json.dumps(
            {
                "workspace_id": self.workspace_id,
                "relative_path": self.relative_path,
                "html_sha256": self.html_sha256,
                "workspace_revision": self.workspace_revision,
                "verifier_digest": self.verifier_digest,
            },
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class BrowserVerificationRequest:
    page_revision: PageRevisionKey
    html: bytes


@dataclass(frozen=True, slots=True)
class BrowserVerification:
    page_status: PageStatus
    diagnostics: tuple[str, ...] = ()


class BrowserVerifier(Protocol):
    async def verify(self, request: BrowserVerificationRequest) -> BrowserVerification: ...


@dataclass(frozen=True, slots=True)
class PageVerificationResult:
    page_revision: PageRevisionKey | None
    execution_status: PageVerificationExecutionStatus
    page_status: PageStatus | None
    diagnostics: tuple[str, ...]
    inject_result: bool


class PageVerificationAutomation:
    def __init__(
        self,
        *,
        mode: PageVerificationMode | str,
        verifier: BrowserVerifier,
        verifier_digest: str,
        max_diagnostics: int = 20,
        max_diagnostic_chars: int = 500,
    ) -> None:
        if max_diagnostics < 1 or max_diagnostic_chars < 1:
            raise ValueError("diagnostic limits must be positive")
        self._mode = PageVerificationMode(mode)
        self._verifier = verifier
        self._verifier_digest = verifier_digest
        self._max_diagnostics = max_diagnostics
        self._max_diagnostic_chars = max_diagnostic_chars
        self._cache: dict[PageRevisionKey, PageVerificationResult] = {}
        self._in_flight: dict[PageRevisionKey, asyncio.Future[PageVerificationResult]] = {}
        self._cache_lock = asyncio.Lock()

    async def verify_after_write(
        self,
        *,
        workspace_id: str,
        relative_path: str,
        html: str | bytes,
        workspace_revision: int,
        changed: bool,
    ) -> PageVerificationResult:
        if not changed:
            return PageVerificationResult(
                page_revision=None,
                execution_status=PageVerificationExecutionStatus.SKIPPED_UNCHANGED,
                page_status=None,
                diagnostics=(),
                inject_result=False,
            )

        html_bytes = html.encode("utf-8") if isinstance(html, str) else html
        page_revision = PageRevisionKey(
            workspace_id=workspace_id,
            relative_path=relative_path,
            html_sha256=hashlib.sha256(html_bytes).hexdigest(),
            workspace_revision=workspace_revision,
            verifier_digest=self._verifier_digest,
        )
        if self._mode is PageVerificationMode.DISABLED:
            return PageVerificationResult(
                page_revision=page_revision,
                execution_status=PageVerificationExecutionStatus.DISABLED,
                page_status=None,
                diagnostics=(),
                inject_result=False,
            )
        async with self._cache_lock:
            cached = self._cache.get(page_revision)
            pending = self._in_flight.get(page_revision)
            owns_verification = cached is None and pending is None
            if owns_verification:
                pending = asyncio.get_running_loop().create_future()
                self._in_flight[page_revision] = pending

        if cached is not None:
            return PageVerificationResult(
                page_revision=page_revision,
                execution_status=PageVerificationExecutionStatus.CACHED,
                page_status=cached.page_status,
                diagnostics=cached.diagnostics,
                inject_result=False,
            )
        if not owns_verification:
            if pending is None:
                raise RuntimeError("page verification cache invariant violated")
            try:
                completed = await asyncio.shield(pending)
            except asyncio.CancelledError:
                return PageVerificationResult(
                    page_revision=page_revision,
                    execution_status=PageVerificationExecutionStatus.CANCELLED,
                    page_status=None,
                    diagnostics=(),
                    inject_result=False,
                )
            return PageVerificationResult(
                page_revision=page_revision,
                execution_status=PageVerificationExecutionStatus.CACHED,
                page_status=completed.page_status,
                diagnostics=completed.diagnostics,
                inject_result=False,
            )

        try:
            verification = await self._verifier.verify(
                BrowserVerificationRequest(page_revision=page_revision, html=html_bytes)
            )
            result = PageVerificationResult(
                page_revision=page_revision,
                execution_status=PageVerificationExecutionStatus.EXECUTED,
                page_status=verification.page_status,
                diagnostics=self._limit_diagnostics(verification.diagnostics),
                inject_result=True,
            )
        except asyncio.CancelledError:
            result = PageVerificationResult(
                page_revision=page_revision,
                execution_status=PageVerificationExecutionStatus.CANCELLED,
                page_status=None,
                diagnostics=(),
                inject_result=True,
            )
        except Exception as error:
            result = PageVerificationResult(
                page_revision=page_revision,
                execution_status=PageVerificationExecutionStatus.FAILED,
                page_status=None,
                diagnostics=self._limit_diagnostics((f"{type(error).__name__}: {error}",)),
                inject_result=True,
            )
        async with self._cache_lock:
            self._cache[page_revision] = result
            pending = self._in_flight.pop(page_revision)
            pending.set_result(result)
        return result

    def _limit_diagnostics(self, diagnostics: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(
            diagnostic[: self._max_diagnostic_chars]
            for diagnostic in diagnostics[: self._max_diagnostics]
        )
