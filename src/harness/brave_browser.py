"""Concrete Brave browser capability, driven over CDP.

Brave is a Chromium build, so it already speaks the DevTools Protocol over a
WebSocket and ``aiohttp`` is already a dependency: no browser-automation library
is needed to open a page and read its DOM.

Two invariants come from the contract and are enforced here, not by convention:

* every operation gets its own process and its own throwaway ``--user-data-dir``,
  so nothing — cookies, storage, cache, service workers — survives or is shared
  between web access and page verification (ADR-0003);
* the browser never resolves DNS itself. The destination is resolved and vetted
  by the same :class:`~harness.web_tools.EgressGuard` that guards the HTTP path,
  and the validated address is pinned with ``--host-resolver-rules``, which also
  makes every other host unresolvable for the lifetime of the process.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import shutil
import signal
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import aiohttp

from .page_verification import (
    BrowserVerification,
    BrowserVerificationRequest,
    PageStatus,
)
from .ports import EngineReadiness
from .web_tools import (
    BrowserPage,
    EgressGuard,
    EgressPolicyError,
    EgressResolutionError,
)

# Any Chromium answers the same DevTools Protocol. Brave stays first because a
# host that already recorded runs with it must keep picking the same binary.
CANDIDATE_EXECUTABLES = (
    "brave-browser",
    "brave-browser-stable",
    "brave",
    "chromium",
    "chromium-browser",
    "google-chrome",
)

_LAUNCH_FLAGS = (
    "--headless=new",
    "--disable-gpu",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-extensions",
    "--disable-background-networking",
    "--disable-component-update",
    "--disable-sync",
    "--disable-domain-reliability",
    "--disable-client-side-phishing-detection",
    "--metrics-recording-only",
    "--no-pings",
    "--mute-audio",
    "--remote-debugging-port=0",
)

_DEVTOOLS_PORT_FILE = "DevToolsActivePort"


class BraveBrowserError(OSError):
    """Raised for every browser failure.

    Deliberately an ``OSError``: ``WebToolExecutor._browser_escalation`` already
    turns those into a failed ResultPayload, so a browser problem degrades the
    tool call instead of failing the whole Turn.
    """


async def _terminate_group(process: asyncio.subprocess.Process, timeout_seconds: float) -> None:
    """Stop the browser and everything it forked.

    SIGTERM to the leader lets Chromium reap its own zygotes and crashpad handler;
    SIGKILL on the group is the backstop for a browser that ignores it.
    """
    # Read the group id before waiting: once the leader is reaped its pid is gone
    # and getpgid would fail, leaving every zygote alive.
    try:
        group = os.getpgid(process.pid)
    except ProcessLookupError:
        return
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(group, signal.SIGTERM)
    try:
        async with asyncio.timeout(timeout_seconds):
            await process.wait()
    except TimeoutError:
        pass
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(group, signal.SIGKILL)
    with contextlib.suppress(Exception):
        await process.wait()
    await _await_group_exit(group, timeout_seconds)


async def _await_group_exit(group: int, timeout_seconds: float) -> None:
    """Wait until the group is gone, so the profile can be removed for good.

    Deleting the directory while a child is still flushing to it recreates it.
    """
    try:
        async with asyncio.timeout(timeout_seconds):
            while True:
                try:
                    os.killpg(group, 0)
                except (ProcessLookupError, PermissionError):
                    return
                await asyncio.sleep(0.02)
    except TimeoutError:
        return


def find_brave_executable(candidates: Sequence[str] = CANDIDATE_EXECUTABLES) -> str | None:
    for candidate in candidates:
        resolved = shutil.which(candidate)
        if resolved is not None:
            return resolved
    return None


class BraveEgressGuard:
    """Readiness probe and identity of the installed Chromium binary."""

    def __init__(
        self,
        *,
        executable: str | Path | None = None,
        timeout_seconds: float = 10.0,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self._configured = str(executable) if executable is not None else None
        self._timeout_seconds = timeout_seconds
        self._version: str | None = None

    @property
    def executable(self) -> str | None:
        if self._configured is not None:
            return self._configured if Path(self._configured).exists() else None
        return find_brave_executable()

    async def readiness(self) -> EngineReadiness:
        executable = self.executable
        if executable is None:
            return EngineReadiness(ready=False, reason_code="browser_executable_missing")
        try:
            await self.version()
        except BraveBrowserError:
            return EngineReadiness(ready=False, reason_code="browser_version_unavailable")
        return EngineReadiness(ready=True)

    async def version(self) -> str:
        if self._version is not None:
            return self._version
        executable = self.executable
        if executable is None:
            raise BraveBrowserError("No Chromium executable was found.")
        process = await asyncio.create_subprocess_exec(
            executable,
            "--version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            async with asyncio.timeout(self._timeout_seconds):
                stdout, _ = await process.communicate()
        except TimeoutError as error:
            process.kill()
            raise BraveBrowserError("The browser did not report its version.") from error
        if process.returncode != 0:
            raise BraveBrowserError("The browser exited with an error while reporting its version.")
        self._version = stdout.decode("utf-8", "replace").strip()
        return self._version

    async def digest(self) -> str:
        """Stable identity of the verifier, for the PageRevision key."""
        return hashlib.sha256(f"brave\n{await self.version()}".encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class _Endpoint:
    port: int
    websocket_path: str


class _BraveProcess:
    """One browser process with one throwaway profile directory."""

    def __init__(
        self,
        *,
        executable: str,
        resolver_rules: str,
        startup_timeout_seconds: float,
        shutdown_timeout_seconds: float = 5.0,
    ) -> None:
        self._executable = executable
        self._resolver_rules = resolver_rules
        self._startup_timeout_seconds = startup_timeout_seconds
        self._shutdown_timeout_seconds = shutdown_timeout_seconds
        self._profile: Path | None = None
        self._process: asyncio.subprocess.Process | None = None

    async def __aenter__(self) -> _Endpoint:
        self._profile = Path(tempfile.mkdtemp(prefix="harness-brave-"))
        try:
            self._process = await asyncio.create_subprocess_exec(
                self._executable,
                *_LAUNCH_FLAGS,
                f"--user-data-dir={self._profile}",
                f"--host-resolver-rules={self._resolver_rules}",
                "about:blank",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                # Own process group: Chromium spawns zygotes and a crashpad handler,
                # and killing only the leader orphans every one of them.
                start_new_session=True,
            )
            return await self._await_endpoint()
        except BaseException:
            await self.__aexit__(None, None, None)
            raise

    async def __aexit__(self, *_exception: object) -> None:
        process = self._process
        self._process = None
        if process is not None and process.returncode is None:
            await _terminate_group(process, self._shutdown_timeout_seconds)
        profile = self._profile
        self._profile = None
        if profile is not None:
            shutil.rmtree(profile, ignore_errors=True)

    async def _await_endpoint(self) -> _Endpoint:
        assert self._profile is not None
        port_file = self._profile / _DEVTOOLS_PORT_FILE
        try:
            async with asyncio.timeout(self._startup_timeout_seconds):
                while True:
                    if self._process is not None and self._process.returncode is not None:
                        raise BraveBrowserError(
                            "The browser exited before opening a debugging port."
                        )
                    if port_file.exists():
                        lines = port_file.read_text("utf-8").splitlines()
                        if len(lines) >= 2 and lines[0].strip().isdigit():
                            return _Endpoint(
                                port=int(lines[0].strip()),
                                websocket_path=lines[1].strip(),
                            )
                    await asyncio.sleep(0.02)
        except TimeoutError as error:
            raise BraveBrowserError("The browser did not open a debugging port in time.") from error


class _CdpConnection:
    """Minimal CDP client: sequential commands and event waiting on one socket."""

    def __init__(self, socket: aiohttp.ClientWebSocketResponse) -> None:
        self._socket = socket
        self._next_id = 0
        self._events: list[Mapping[str, Any]] = []

    @property
    def events(self) -> tuple[Mapping[str, Any], ...]:
        return tuple(self._events)

    async def call(
        self,
        method: str,
        params: Mapping[str, Any] | None = None,
        *,
        session_id: str | None = None,
    ) -> Mapping[str, Any]:
        self._next_id += 1
        message_id = self._next_id
        message: dict[str, Any] = {"id": message_id, "method": method, "params": dict(params or {})}
        if session_id is not None:
            message["sessionId"] = session_id
        await self._socket.send_str(json.dumps(message))
        while True:
            payload = await self._receive()
            if payload.get("id") != message_id:
                continue
            error = payload.get("error")
            if error is not None:
                raise BraveBrowserError(f"CDP command failed: {method}: {error}")
            return cast(Mapping[str, Any], payload.get("result") or {})

    async def wait_for_event(self, method: str) -> Mapping[str, Any]:
        for event in self._events:
            if event.get("method") == method:
                return event
        while True:
            payload = await self._receive()
            if payload.get("method") == method:
                return payload

    async def _receive(self) -> Mapping[str, Any]:
        message = await self._socket.receive()
        if message.type is not aiohttp.WSMsgType.TEXT:
            raise BraveBrowserError("The browser closed the debugging connection.")
        payload = cast(Mapping[str, Any], json.loads(message.data))
        if "id" not in payload:
            self._events.append(payload)
        return payload


class _BraveSession:
    """A page attached to a freshly launched browser, closed with it."""

    def __init__(
        self,
        *,
        executable: str,
        resolver_rules: str,
        startup_timeout_seconds: float,
    ) -> None:
        self._process = _BraveProcess(
            executable=executable,
            resolver_rules=resolver_rules,
            startup_timeout_seconds=startup_timeout_seconds,
        )
        self._exit_stack: list[Any] = []
        self.connection: _CdpConnection
        self.session_id: str

    async def __aenter__(self) -> _BraveSession:
        endpoint = await self._process.__aenter__()
        try:
            client = aiohttp.ClientSession(
                cookie_jar=aiohttp.DummyCookieJar(),
                trust_env=False,
            )
            self._exit_stack.append(client)
            socket = await client.ws_connect(
                f"ws://127.0.0.1:{endpoint.port}{endpoint.websocket_path}",
                max_msg_size=0,
            )
            self._exit_stack.append(socket)
            self.connection = _CdpConnection(socket)
            target = await self.connection.call("Target.createTarget", {"url": "about:blank"})
            attached = await self.connection.call(
                "Target.attachToTarget",
                {"targetId": target["targetId"], "flatten": True},
            )
            self.session_id = cast(str, attached["sessionId"])
            return self
        except BaseException:
            await self.__aexit__(None, None, None)
            raise

    async def __aexit__(self, *_exception: object) -> None:
        while self._exit_stack:
            closeable = self._exit_stack.pop()
            with contextlib.suppress(Exception):
                await closeable.close()
        await self._process.__aexit__()

    async def call(self, method: str, params: Mapping[str, Any] | None = None) -> Mapping[str, Any]:
        return await self.connection.call(method, params, session_id=self.session_id)

    async def evaluate(self, expression: str) -> Any:
        result = await self.call(
            "Runtime.evaluate",
            {"expression": expression, "returnByValue": True, "awaitPromise": False},
        )
        details = result.get("exceptionDetails")
        if details is not None:
            raise BraveBrowserError(f"Page evaluation failed: {details}")
        return cast(Mapping[str, Any], result.get("result") or {}).get("value")


class BraveBrowserCapability:
    """Loads a page in an isolated browser process and returns its rendered DOM."""

    def __init__(
        self,
        *,
        egress_guard: EgressGuard | None = None,
        executable: str | Path | None = None,
        guard: BraveEgressGuard | None = None,
        startup_timeout_seconds: float = 20.0,
    ) -> None:
        self._egress_guard = egress_guard or EgressGuard()
        self._guard = guard or BraveEgressGuard(executable=executable)
        self._startup_timeout_seconds = startup_timeout_seconds

    async def fetch(
        self,
        url: str,
        *,
        max_bytes: int,
        timeout_seconds: float,
    ) -> BrowserPage:
        executable = self._guard.executable
        if executable is None:
            raise BraveBrowserError("No Chromium executable was found.")
        try:
            target = await self._egress_guard.resolve(url)
        except EgressPolicyError as error:
            # The policy layer above only understands OSError; keep the code readable there.
            raise BraveBrowserError(
                f"Egress policy rejected the browser target: {error}"
            ) from error
        except EgressResolutionError as error:
            raise BraveBrowserError("The browser target could not be resolved.") from error
        if not target.addresses:
            raise BraveBrowserError("The browser target resolved to no usable address.")

        async with _BraveSession(
            executable=executable,
            resolver_rules=host_resolver_rules(target.hostname, target.addresses[0].host),
            startup_timeout_seconds=self._startup_timeout_seconds,
        ) as session:
            async with asyncio.timeout(timeout_seconds):
                await session.call("Page.enable")
                await session.call("Page.navigate", {"url": target.url})
                await session.connection.wait_for_event("Page.loadEventFired")
                html = await session.evaluate("document.documentElement.outerHTML")
                final_url = await session.evaluate("location.href")

        if not isinstance(html, str):
            raise BraveBrowserError("The browser returned no document.")
        # Documento grande demais é cortado, não descartado: a extração e o
        # `limit` do web_fetch já entregam bem menos do que isso ao modelo.
        body = html.encode("utf-8")[:max_bytes]
        return BrowserPage(
            final_url=final_url if isinstance(final_url, str) and final_url else target.url,
            content_type="text/html; charset=utf-8",
            body=body,
        )


class BraveBrowserVerifier:
    """Loads written HTML in its own browser process and reports load errors.

    ponytail: judges a page by JavaScript exceptions and severe console entries,
    ignoring network failures because the profile resolves no host at all. Good
    enough while page_verification is disabled by contract; promoting the mode
    means measuring this verifier against real pages first.
    """

    def __init__(
        self,
        *,
        guard: BraveEgressGuard | None = None,
        executable: str | Path | None = None,
        startup_timeout_seconds: float = 20.0,
        timeout_seconds: float = 30.0,
    ) -> None:
        self._guard = guard or BraveEgressGuard(executable=executable)
        self._startup_timeout_seconds = startup_timeout_seconds
        self._timeout_seconds = timeout_seconds

    @property
    def guard(self) -> BraveEgressGuard:
        return self._guard

    async def verify(self, request: BrowserVerificationRequest) -> BrowserVerification:
        executable = self._guard.executable
        if executable is None:
            raise BraveBrowserError("No Chromium executable was found.")
        html = request.html.decode("utf-8", "replace")

        async with _BraveSession(
            executable=executable,
            # Verification never leaves the machine: nothing resolves.
            resolver_rules="MAP * ~NOTFOUND",
            startup_timeout_seconds=self._startup_timeout_seconds,
        ) as session:
            async with asyncio.timeout(self._timeout_seconds):
                await session.call("Page.enable")
                await session.call("Runtime.enable")
                await session.call("Log.enable")
                frame_tree = await session.call("Page.getFrameTree")
                frame_id = cast(Mapping[str, Any], frame_tree["frameTree"])["frame"]["id"]
                await session.call(
                    "Page.setDocumentContent",
                    {"frameId": frame_id, "html": html},
                )
                # setDocumentContent parses synchronously; give queued log events a turn.
                await asyncio.sleep(0.1)
                diagnostics = _page_diagnostics(session.connection.events)

        return BrowserVerification(
            page_status=PageStatus.INVALID if diagnostics else PageStatus.VALID,
            diagnostics=diagnostics,
        )


def host_resolver_rules(hostname: str, address: str) -> str:
    """Pin one hostname to one vetted address and make every other host unresolvable."""
    return f"MAP {hostname} {address},MAP * ~NOTFOUND"


def _page_diagnostics(events: Sequence[Mapping[str, Any]]) -> tuple[str, ...]:
    diagnostics: list[str] = []
    for event in events:
        method = event.get("method")
        params = cast(Mapping[str, Any], event.get("params") or {})
        if method == "Runtime.exceptionThrown":
            details = cast(Mapping[str, Any], params.get("exceptionDetails") or {})
            diagnostics.append(f"exception: {details.get('text', 'unknown')}")
        elif method == "Log.entryAdded":
            entry = cast(Mapping[str, Any], params.get("entry") or {})
            if entry.get("level") == "error" and entry.get("source") != "network":
                diagnostics.append(f"{entry.get('source', 'log')}: {entry.get('text', '')}")
    return tuple(diagnostics)
