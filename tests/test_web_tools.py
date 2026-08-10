import asyncio
import json
import socket
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime

from harness import (
    AiohttpHttpTransport,
    BrowserPage,
    EgressGuard,
    EgressPolicyError,
    EngineReadiness,
    Grant,
    GuardedResolver,
    HttpResponse,
    ResolvedAddress,
    ResolvedTarget,
    SessionPolicy,
    ToolCall,
    WebToolExecutor,
    load_config,
)


class FakeLookup:
    def __init__(self, addresses: Sequence[tuple[int, str]]) -> None:
        self.addresses = addresses
        self.calls: list[tuple[str, int]] = []

    async def __call__(
        self,
        host: str,
        port: int,
        family: int,
        type_: int,
    ) -> Sequence[tuple[int, int, int, str, tuple[str, int] | tuple[str, int, int, int]]]:
        del family, type_
        self.calls.append((host, port))
        return tuple(
            (
                address_family,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                (address, port) if address_family == socket.AF_INET else (address, port, 0, 0),
            )
            for address_family, address in self.addresses
        )


class FakeHttpTransport:
    def __init__(self, responses: Sequence[HttpResponse]) -> None:
        self.responses = list(responses)
        self.requests: list[tuple[ResolvedTarget, Mapping[str, str]]] = []

    async def request(
        self,
        target: ResolvedTarget,
        *,
        headers: Mapping[str, str],
        max_bytes: int,
        timeout_seconds: float,
    ) -> HttpResponse:
        del max_bytes, timeout_seconds
        self.requests.append((target, headers))
        return self.responses.pop(0)


class SlowHttpTransport:
    async def request(
        self,
        target: ResolvedTarget,
        *,
        headers: Mapping[str, str],
        max_bytes: int,
        timeout_seconds: float,
    ) -> HttpResponse:
        del target, headers, max_bytes, timeout_seconds
        await asyncio.sleep(1)
        raise AssertionError("The executor timeout must cancel the transport")


class FakeBrowser:
    def __init__(self, page: BrowserPage) -> None:
        self.page = page
        self.urls: list[str] = []

    async def fetch(
        self,
        url: str,
        *,
        max_bytes: int,
        timeout_seconds: float,
    ) -> BrowserPage:
        del max_bytes, timeout_seconds
        self.urls.append(url)
        return self.page


class FakeBrowserGuard:
    def __init__(self, ready: bool) -> None:
        self.ready = ready

    async def readiness(self) -> EngineReadiness:
        return EngineReadiness(ready=self.ready, reason_code=None if self.ready else "not_ready")


def web_policy(*permissions: str) -> SessionPolicy:
    now = datetime.now(UTC)
    return SessionPolicy(
        conversation_id="conversation-1",
        grants=tuple(
            Grant(
                id=f"grant-{permission}",
                conversation_id="conversation-1",
                permission=permission,
                scope="public-web",
                granted_at=now,
            )
            for permission in permissions
        ),
    )


def public_guard() -> EgressGuard:
    return EgressGuard(GuardedResolver(lookup=FakeLookup(((socket.AF_INET, "93.184.216.34"),))))


def test_egress_guard_rejects_non_http_userinfo_localhost_and_non_public_ips() -> None:
    async def scenario() -> None:
        blocked_urls = {
            "ftp://example.com/file": "disallowed_url_scheme",
            "https://operator:secret@example.com/": "url_userinfo_not_allowed",
            "http://localhost/": "localhost_not_allowed",
            "http://127.0.0.1/": "non_public_address",
            "http://10.0.0.1/": "non_public_address",
            "http://169.254.1.1/": "non_public_address",
            "http://100.64.0.1/": "non_public_address",
            "http://224.0.0.1/": "non_public_address",
            "http://240.0.0.1/": "non_public_address",
            "http://0.0.0.0/": "non_public_address",
            "http://[::1]/": "non_public_address",
            "http://[fc00::1]/": "non_public_address",
            "http://[fe80::1]/": "non_public_address",
            "http://[ff02::1]/": "non_public_address",
            "http://[::]/": "non_public_address",
        }

        for url, expected_code in blocked_urls.items():
            lookup = FakeLookup(
                ((socket.AF_INET6, url.split("[")[1].split("]")[0]),)
                if "[" in url
                else ((socket.AF_INET, url.split("//")[-1].split("/")[0]),)
            )
            guard = EgressGuard(GuardedResolver(lookup=lookup))
            try:
                await guard.resolve(url)
            except EgressPolicyError as error:
                assert error.code == expected_code
            else:
                raise AssertionError(f"Expected {url} to be rejected")

    asyncio.run(scenario())


def test_guarded_resolver_rejects_entire_hostname_when_one_result_is_private() -> None:
    async def scenario() -> None:
        lookup = FakeLookup(
            (
                (socket.AF_INET, "93.184.216.34"),
                (socket.AF_INET6, "2001:4860:4860::8888"),
                (socket.AF_INET, "192.168.1.20"),
            )
        )
        guard = EgressGuard(GuardedResolver(lookup=lookup))

        try:
            await guard.resolve("https://example.com/resource")
        except EgressPolicyError as error:
            assert error.code == "non_public_address"
        else:
            raise AssertionError("Expected mixed DNS results to be rejected")
        assert lookup.calls == [("example.com", 443)]

    asyncio.run(scenario())


def test_web_preflight_requires_grant_and_validates_registry_schema() -> None:
    async def scenario() -> None:
        transport = FakeHttpTransport(())
        missing_grant = WebToolExecutor(
            registry=load_config().tool_registry,
            session_policy=web_policy(),
            egress_guard=public_guard(),
            http_transport=transport,
            brave_api_key="not-used",
        )
        granted = WebToolExecutor(
            registry=load_config().tool_registry,
            session_policy=web_policy("WebAccessGrant"),
            egress_guard=public_guard(),
            http_transport=transport,
            brave_api_key="not-used",
        )

        grant_result = await missing_grant.preflight(
            (ToolCall(id="fetch", name="web_fetch", arguments={"url": "https://example.com"}),)
        )
        schema_result = await granted.preflight(
            (
                ToolCall(
                    id="search",
                    name="web_search",
                    arguments={"query": "harness", "unexpected": True},
                ),
            )
        )

        assert grant_result.allowed is False
        assert grant_result.reason_code == "web_access_grant_required"
        assert schema_result.allowed is False
        assert schema_result.reason_code == "invalid_tool_arguments"
        assert transport.requests == []

    asyncio.run(scenario())


def test_web_fetch_revalidates_redirect_before_following_it() -> None:
    async def scenario() -> None:
        transport = FakeHttpTransport(
            (HttpResponse(status=302, headers={"Location": "http://127.0.0.1/secret"}, body=b""),)
        )
        executor = WebToolExecutor(
            registry=load_config().tool_registry,
            session_policy=web_policy("WebAccessGrant"),
            egress_guard=public_guard(),
            http_transport=transport,
            brave_api_key=None,
        )

        result = await executor.execute(
            ToolCall(id="fetch", name="web_fetch", arguments={"url": "https://example.com"})
        )

        assert result.status.value == "blocked"
        assert result.error is not None
        assert result.error["code"] == "non_public_address"
        assert len(transport.requests) == 1

    asyncio.run(scenario())


def test_web_fetch_extracts_html_once_and_discards_raw_markup() -> None:
    async def scenario() -> None:
        html = b"""
            <html><head><title>Useful reference</title><style>hidden style</style></head>
            <body><nav>hidden navigation</nav><main>
            <h1>Harness security reference</h1>
            <p>This public article contains a deliberately substantial explanation of guarded
            network access, deterministic fetching, and safe handling of untrusted web content.</p>
            <p>Read the <a href="/details">detailed implementation notes</a> for examples.</p>
            <script>hidden script</script><aside>hidden aside</aside></main>
            <footer>hidden footer</footer></body></html>
        """
        transport = FakeHttpTransport(
            (
                HttpResponse(
                    status=200,
                    headers={"Content-Type": "text/html; charset=utf-8"},
                    body=html,
                ),
            )
        )
        executor = WebToolExecutor(
            registry=load_config().tool_registry,
            session_policy=web_policy("WebAccessGrant"),
            egress_guard=public_guard(),
            http_transport=transport,
            brave_api_key=None,
        )
        call = ToolCall(
            id="fetch",
            name="web_fetch",
            arguments={"url": "https://example.com/article", "limit": 12000},
        )

        result = await executor.execute(call)

        assert result.status.value == "success"
        assert isinstance(result.data, Mapping)
        content = result.data["content"]
        assert isinstance(content, str)
        assert "Harness security reference" in content
        assert "detailed implementation notes (https://example.com/details)" in content
        assert all(
            hidden not in content
            for hidden in (
                "hidden style",
                "hidden navigation",
                "hidden script",
                "hidden aside",
                "hidden footer",
            )
        )
        assert "<html" not in repr(result)
        assert result.meta == {
            "producer": "web_fetch",
            "truncated": False,
            "taints": ["UntrustedWebTaint"],
            "final_url": "https://example.com/article",
            "cache_hit": False,
        }

    asyncio.run(scenario())


def test_web_fetch_enforces_body_cap_and_reuses_exact_url_cache() -> None:
    async def scenario() -> None:
        oversized_transport = FakeHttpTransport(
            (HttpResponse(status=200, headers={"Content-Type": "text/plain"}, body=b"x" * 65),)
        )
        oversized_executor = WebToolExecutor(
            registry=load_config().tool_registry,
            session_policy=web_policy("WebAccessGrant"),
            egress_guard=public_guard(),
            http_transport=oversized_transport,
            brave_api_key=None,
            max_response_bytes=64,
        )
        oversized = await oversized_executor.execute(
            ToolCall(id="large", name="web_fetch", arguments={"url": "https://example.com/large"})
        )
        assert oversized.status.value == "blocked"
        assert oversized.error is not None
        assert oversized.error["code"] == "response_byte_limit_exceeded"

        content = (
            b"A sufficiently long plain text response demonstrates exact URL caching without "
            b"making a second HTTP request during the same executor instance and Turn."
        )
        cached_transport = FakeHttpTransport(
            (HttpResponse(status=200, headers={"Content-Type": "text/plain"}, body=content),)
        )
        cached_executor = WebToolExecutor(
            registry=load_config().tool_registry,
            session_policy=web_policy("WebAccessGrant"),
            egress_guard=public_guard(),
            http_transport=cached_transport,
            brave_api_key=None,
        )
        first = await cached_executor.execute(
            ToolCall(id="first", name="web_fetch", arguments={"url": "https://example.com/cache"})
        )
        second = await cached_executor.execute(
            ToolCall(id="second", name="web_fetch", arguments={"url": "https://example.com/cache"})
        )

        assert first.status.value == "success"
        assert second.status.value == "success"
        assert second.tool_call_id == "second"
        assert second.meta["cache_hit"] is True
        assert len(cached_transport.requests) == 1

    asyncio.run(scenario())


def test_web_fetch_short_extraction_and_missing_browser_capability_are_failed() -> None:
    async def scenario() -> None:
        short_executor = WebToolExecutor(
            registry=load_config().tool_registry,
            session_policy=web_policy("WebAccessGrant"),
            egress_guard=public_guard(),
            http_transport=FakeHttpTransport(
                (
                    HttpResponse(
                        status=200,
                        headers={"Content-Type": "text/html"},
                        body=b"<p>short</p>",
                    ),
                )
            ),
            brave_api_key=None,
        )
        short = await short_executor.execute(
            ToolCall(id="short", name="web_fetch", arguments={"url": "https://example.com/short"})
        )
        assert short.status.value == "failed"
        assert short.error is not None
        assert short.error["code"] == "empty_extraction"

        unavailable_executor = WebToolExecutor(
            registry=load_config().tool_registry,
            session_policy=web_policy("WebAccessGrant"),
            egress_guard=public_guard(),
            http_transport=FakeHttpTransport(
                (
                    HttpResponse(
                        status=403,
                        headers={"Content-Type": "text/html"},
                        body=b"challenge",
                    ),
                )
            ),
            brave_api_key=None,
        )
        unavailable = await unavailable_executor.execute(
            ToolCall(
                id="browser",
                name="web_fetch",
                arguments={"url": "https://example.com/challenge"},
            )
        )
        assert unavailable.status.value == "failed"
        assert unavailable.error is not None
        assert unavailable.error["code"] == "browser_escalation_unavailable"

    asyncio.run(scenario())


def test_web_fetch_enforces_redirect_content_type_and_character_limits() -> None:
    async def scenario() -> None:
        redirect_transport = FakeHttpTransport(
            (
                HttpResponse(status=302, headers={"Location": "/one"}, body=b""),
                HttpResponse(status=302, headers={"Location": "/two"}, body=b""),
            )
        )
        redirect_executor = WebToolExecutor(
            registry=load_config().tool_registry,
            session_policy=web_policy("WebAccessGrant"),
            egress_guard=public_guard(),
            http_transport=redirect_transport,
            max_redirects=1,
        )
        redirected = await redirect_executor.execute(
            ToolCall(
                id="redirects",
                name="web_fetch",
                arguments={"url": "https://example.com/start"},
            )
        )
        assert redirected.status.value == "blocked"
        assert redirected.error is not None
        assert redirected.error["code"] == "redirect_limit_exceeded"
        assert len(redirect_transport.requests) == 2
        assert len({headers["User-Agent"] for _, headers in redirect_transport.requests}) == 1

        unsupported_executor = WebToolExecutor(
            registry=load_config().tool_registry,
            session_policy=web_policy("WebAccessGrant"),
            egress_guard=public_guard(),
            http_transport=FakeHttpTransport(
                (
                    HttpResponse(
                        status=200,
                        headers={"Content-Type": "image/png"},
                        body=b"not an image",
                    ),
                )
            ),
        )
        unsupported = await unsupported_executor.execute(
            ToolCall(
                id="content-type",
                name="web_fetch",
                arguments={"url": "https://example.com/image"},
            )
        )
        assert unsupported.status.value == "failed"
        assert unsupported.error is not None
        assert unsupported.error["code"] == "unsupported_content_type"

        long_content = b"x" * 140
        limited_executor = WebToolExecutor(
            registry=load_config().tool_registry,
            session_policy=web_policy("WebAccessGrant"),
            egress_guard=public_guard(),
            http_transport=FakeHttpTransport(
                (
                    HttpResponse(
                        status=200,
                        headers={"Content-Type": "text/plain"},
                        body=long_content,
                    ),
                )
            ),
        )
        limited = await limited_executor.execute(
            ToolCall(
                id="character-limit",
                name="web_fetch",
                arguments={"url": "https://example.com/long", "limit": 100},
            )
        )
        assert limited.status.value == "success"
        assert isinstance(limited.data, Mapping)
        assert limited.data["content"] == "x" * 100
        assert limited.meta["truncated"] is True
        assert limited.meta["continuation"] == {"offset": 100}

    asyncio.run(scenario())


def test_web_search_uses_brave_api_limits_results_and_normalizes_cache_key() -> None:
    async def scenario() -> None:
        provider_body = json.dumps(
            {
                "web": {
                    "results": [
                        {"title": "One", "url": "https://one.example", "description": "First"},
                        {"title": "Two", "url": "https://two.example", "description": "Second"},
                        {"title": "Three", "url": "https://three.example", "description": "Third"},
                    ]
                },
                "query": {"more_results_available": True},
            }
        ).encode()
        transport = FakeHttpTransport(
            (
                HttpResponse(
                    status=200,
                    headers={"Content-Type": "application/json"},
                    body=provider_body,
                ),
            )
        )
        executor = WebToolExecutor(
            registry=load_config().tool_registry,
            session_policy=web_policy("WebAccessGrant"),
            egress_guard=public_guard(),
            http_transport=transport,
            brave_api_key="brave-secret",
        )

        first = await executor.execute(
            ToolCall(
                id="search-1",
                name="web_search",
                arguments={"query": "  Harness   Security  ", "limit": 2},
            )
        )
        second = await executor.execute(
            ToolCall(
                id="search-2",
                name="web_search",
                arguments={"query": "harness security", "limit": 2},
            )
        )

        assert first.status.value == "success"
        assert isinstance(first.data, Mapping)
        results = first.data["results"]
        assert isinstance(results, Sequence)
        assert len(results) == 2
        assert first.meta == {
            "producer": "brave_search",
            "truncated": True,
            "taints": ["UntrustedWebTaint"],
            "engine": "brave",
            "cache_hit": False,
            "continuation": {"offset": 2},
        }
        assert second.tool_call_id == "search-2"
        assert second.meta["cache_hit"] is True
        assert len(transport.requests) == 1
        target, headers = transport.requests[0]
        assert target.url.startswith("https://api.search.brave.com/res/v1/web/search?")
        assert "q=Harness+Security" in target.url
        assert "count=2" in target.url
        assert headers["X-Subscription-Token"] == "brave-secret"
        assert headers["User-Agent"].startswith("Harness/2.0")

    asyncio.run(scenario())


def test_brave_auth_and_rate_limit_are_failed_without_secret_disclosure() -> None:
    async def scenario() -> None:
        secret = "must-never-appear-in-result"
        auth_executor = WebToolExecutor(
            registry=load_config().tool_registry,
            session_policy=web_policy("WebAccessGrant"),
            egress_guard=public_guard(),
            http_transport=FakeHttpTransport(
                (
                    HttpResponse(
                        status=401,
                        headers={"Content-Type": "application/json"},
                        body=b"{}",
                    ),
                )
            ),
            brave_api_key=secret,
        )
        auth = await auth_executor.execute(
            ToolCall(id="auth", name="web_search", arguments={"query": "security"})
        )

        assert auth.status.value == "failed"
        assert auth.retryable is False
        assert auth.error is not None
        assert auth.error["code"] == "provider_auth_failed"
        assert secret not in repr(auth)

        rate_executor = WebToolExecutor(
            registry=load_config().tool_registry,
            session_policy=web_policy("WebAccessGrant"),
            egress_guard=public_guard(),
            http_transport=FakeHttpTransport(
                (
                    HttpResponse(
                        status=429,
                        headers={"Content-Type": "application/json"},
                        body=b"{}",
                    ),
                )
            ),
            brave_api_key="another-secret",
        )
        rate = await rate_executor.execute(
            ToolCall(id="rate", name="web_search", arguments={"query": "security"})
        )
        assert rate.status.value == "failed"
        assert rate.retryable is True
        assert rate.error is not None
        assert rate.error["code"] == "provider_rate_limited"

        missing_transport = FakeHttpTransport(())
        missing_executor = WebToolExecutor(
            registry=load_config().tool_registry,
            session_policy=web_policy("WebAccessGrant"),
            egress_guard=public_guard(),
            http_transport=missing_transport,
            brave_api_key=None,
        )
        missing = await missing_executor.execute(
            ToolCall(id="missing", name="web_search", arguments={"query": "security"})
        )
        assert missing.status.value == "failed"
        assert missing.retryable is False
        assert missing.error is not None
        assert missing.error["code"] == "provider_auth_unavailable"
        assert missing_transport.requests == []

    asyncio.run(scenario())


def test_aiohttp_transport_connects_to_pinned_address_without_second_dns_lookup() -> None:
    async def scenario() -> None:
        received = bytearray()

        async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            received.extend(await reader.readuntil(b"\r\n\r\n"))
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: 12\r\n"
                b"Connection: close\r\n\r\npinned route"
            )
            await writer.drain()
            writer.close()
            await writer.wait_closed()

        server = await asyncio.start_server(handle, "127.0.0.1", 0)
        socket_address = server.sockets[0].getsockname()
        port = int(socket_address[1])
        target = ResolvedTarget(
            url=f"http://does-not-resolve.invalid:{port}/resource",
            hostname="does-not-resolve.invalid",
            port=port,
            addresses=(
                ResolvedAddress(
                    host="127.0.0.1",
                    port=port,
                    family=socket.AF_INET,
                    proto=socket.IPPROTO_TCP,
                ),
            ),
        )
        try:
            response = await AiohttpHttpTransport().request(
                target,
                headers={"User-Agent": "Harness/2.0 test"},
                max_bytes=1024,
                timeout_seconds=2,
            )
        finally:
            server.close()
            await server.wait_closed()

        assert response.status == 200
        assert response.body == b"pinned route"
        assert b"Host: does-not-resolve.invalid:" in received
        assert b"User-Agent: Harness/2.0 test" in received
        assert b"Cookie:" not in received

    asyncio.run(scenario())


def test_web_response_timeout_is_enforced_around_injected_transport() -> None:
    async def scenario() -> None:
        executor = WebToolExecutor(
            registry=load_config().tool_registry,
            session_policy=web_policy("WebAccessGrant"),
            egress_guard=public_guard(),
            http_transport=SlowHttpTransport(),
            timeout_seconds=0.01,
        )

        result = await executor.execute(
            ToolCall(id="slow", name="web_fetch", arguments={"url": "https://example.com/slow"})
        )

        assert result.status.value == "failed"
        assert result.retryable is True
        assert result.error is not None
        assert result.error["code"] == "response_timeout"

    asyncio.run(scenario())


def test_browser_capability_opens_only_after_browser_egress_guard_is_ready() -> None:
    async def scenario() -> None:
        page = BrowserPage(
            final_url="https://example.com/rendered",
            content_type="text/html",
            body=(
                b"<main><h1>Rendered article</h1><p>The guarded browser returned enough useful "
                b"content to demonstrate that extraction remains shared with the ordinary HTTP "
                b"path and that raw markup never enters the ToolResult.</p></main>"
            ),
        )
        browser = FakeBrowser(page)
        not_ready = WebToolExecutor(
            registry=load_config().tool_registry,
            session_policy=web_policy("WebAccessGrant"),
            egress_guard=public_guard(),
            http_transport=FakeHttpTransport(
                (
                    HttpResponse(
                        status=403,
                        headers={"Content-Type": "text/html"},
                        body=b"challenge",
                    ),
                )
            ),
            browser_capability=browser,
            browser_egress_guard=FakeBrowserGuard(False),
        )
        denied = await not_ready.execute(
            ToolCall(id="denied", name="web_fetch", arguments={"url": "https://example.com"})
        )
        assert denied.status.value == "failed"
        assert denied.error is not None
        assert denied.error["code"] == "browser_escalation_unavailable"
        assert browser.urls == []

        ready = WebToolExecutor(
            registry=load_config().tool_registry,
            session_policy=web_policy("WebAccessGrant"),
            egress_guard=public_guard(),
            http_transport=FakeHttpTransport(
                (
                    HttpResponse(
                        status=403,
                        headers={"Content-Type": "text/html"},
                        body=b"challenge",
                    ),
                )
            ),
            browser_capability=browser,
            browser_egress_guard=FakeBrowserGuard(True),
        )
        rendered = await ready.execute(
            ToolCall(id="ready", name="web_fetch", arguments={"url": "https://example.com"})
        )
        assert rendered.status.value == "success"
        assert rendered.meta["producer"] == "browser"
        assert browser.urls == ["https://example.com"]
        assert "<main" not in repr(rendered)

    asyncio.run(scenario())
