import asyncio
import socket
import tempfile
from pathlib import Path

import pytest

from harness.brave_browser import (
    BraveBrowserCapability,
    BraveBrowserError,
    BraveBrowserVerifier,
    BraveEgressGuard,
    host_resolver_rules,
)
from harness.page_verification import (
    BrowserVerificationRequest,
    PageRevisionKey,
    PageStatus,
)
from harness.web_tools import (
    EgressGuard,
    EgressPolicyError,
    ResolvedAddress,
    ResolvedTarget,
    ResponseByteLimitError,
)

PAGE = (
    b"<!doctype html><html><head><title>bench</title></head><body>"
    b"<div id='app'>short</div>"
    b"<script>document.getElementById('app').textContent = 'RENDERED-BY-JAVASCRIPT';</script>"
    b"</body></html>"
)

BROKEN_PAGE = b"<!doctype html><html><body><script>null.crash()</script></body></html>"

brave_required = pytest.mark.skipif(
    BraveEgressGuard().executable is None,
    reason="Brave is not installed on this host",
)


class BenchEgressGuard(EgressGuard):
    """Pins a fake public hostname at a loopback bench server.

    The production guard rejects loopback by design; the bench needs a
    deterministic page, so the exception lives in the test, never in the app.
    """

    def __init__(self, port: int) -> None:
        super().__init__()
        self._port = port

    async def resolve(self, url: str) -> ResolvedTarget:
        parsed = self.validate_url(url)
        return ResolvedTarget(
            url=url,
            hostname=parsed.hostname or "",
            port=self._port,
            addresses=(
                ResolvedAddress(
                    host="127.0.0.1",
                    port=self._port,
                    family=socket.AF_INET,
                    proto=socket.IPPROTO_TCP,
                ),
            ),
        )


class RejectingEgressGuard(EgressGuard):
    async def resolve(self, url: str) -> ResolvedTarget:
        del url
        raise EgressPolicyError("non_public_address", "The destination uses a non-public address.")


class EmptyEgressGuard(EgressGuard):
    async def resolve(self, url: str) -> ResolvedTarget:
        return ResolvedTarget(url=url, hostname="bench.test", port=80, addresses=())


async def _bench_server(body: bytes) -> tuple[asyncio.Server, int]:
    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            await reader.readuntil(b"\r\n\r\n")
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nContent-Length: "
                + str(len(body)).encode()
                + b"\r\nConnection: close\r\n\r\n"
                + body
            )
            await writer.drain()
        finally:
            writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    return server, server.sockets[0].getsockname()[1]


def test_host_resolver_rules_pin_one_host_and_deny_every_other() -> None:
    rules = host_resolver_rules("example.test", "93.184.216.34")

    assert rules == "MAP example.test 93.184.216.34,MAP * ~NOTFOUND"
    # Order matters: Chromium applies the first matching rule, so the pin has to win.
    assert rules.index("MAP example.test") < rules.index("MAP * ~NOTFOUND")


def test_missing_executable_is_reported_as_not_ready_without_launching() -> None:
    async def scenario() -> None:
        guard = BraveEgressGuard(executable="/nonexistent/brave")

        readiness = await guard.readiness()

        assert readiness.ready is False
        assert readiness.reason_code == "browser_executable_missing"
        with pytest.raises(BraveBrowserError):
            await guard.version()

    asyncio.run(scenario())


def test_capability_refuses_targets_the_egress_policy_rejects() -> None:
    async def scenario() -> None:
        capability = BraveBrowserCapability(egress_guard=RejectingEgressGuard())

        with pytest.raises(BraveBrowserError):
            await capability.fetch(
                "http://169.254.169.254/latest/meta-data",
                max_bytes=1024,
                timeout_seconds=5,
            )

        empty = BraveBrowserCapability(egress_guard=EmptyEgressGuard())
        with pytest.raises(BraveBrowserError):
            await empty.fetch("http://bench.test/", max_bytes=1024, timeout_seconds=5)

    asyncio.run(scenario())


@brave_required
def test_browser_renders_javascript_and_leaves_no_process_or_profile() -> None:
    temporary = Path(tempfile.gettempdir())
    before = set(temporary.glob("harness-brave-*"))

    async def scenario() -> None:
        server, port = await _bench_server(PAGE)
        async with server:
            capability = BraveBrowserCapability(egress_guard=BenchEgressGuard(port))
            page = await capability.fetch(
                f"http://bench.test:{port}/", max_bytes=2_000_000, timeout_seconds=60
            )

        assert b"RENDERED-BY-JAVASCRIPT" in page.body
        assert page.content_type.startswith("text/html")
        assert page.final_url.startswith("http://bench.test:")

    asyncio.run(scenario())

    assert set(temporary.glob("harness-brave-*")) == before


@brave_required
def test_browser_enforces_the_byte_limit() -> None:
    async def scenario() -> None:
        server, port = await _bench_server(PAGE)
        async with server:
            capability = BraveBrowserCapability(egress_guard=BenchEgressGuard(port))
            with pytest.raises(ResponseByteLimitError):
                await capability.fetch(
                    f"http://bench.test:{port}/", max_bytes=32, timeout_seconds=60
                )

    asyncio.run(scenario())


@brave_required
def test_verifier_separates_a_loading_page_from_a_broken_one() -> None:
    async def scenario() -> None:
        guard = BraveEgressGuard()
        verifier = BraveBrowserVerifier(guard=guard)
        key = PageRevisionKey(
            workspace_id="workspace-1",
            relative_path="index.html",
            html_sha256="0" * 64,
            workspace_revision=1,
            verifier_digest=await guard.digest(),
        )

        valid = await verifier.verify(BrowserVerificationRequest(page_revision=key, html=PAGE))
        invalid = await verifier.verify(
            BrowserVerificationRequest(page_revision=key, html=BROKEN_PAGE)
        )

        assert valid.page_status is PageStatus.VALID
        assert valid.diagnostics == ()
        assert invalid.page_status is PageStatus.INVALID
        assert invalid.diagnostics
        # The digest identifies the verifier, so a PageRevision is tied to it.
        assert len(await guard.digest()) == 64

    asyncio.run(scenario())
