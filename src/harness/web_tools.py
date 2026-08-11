from __future__ import annotations

import asyncio
import ipaddress
import json
import re
import socket
import unicodedata
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from html.parser import HTMLParser
from typing import Any, Protocol, cast
from urllib.parse import SplitResult, urlencode, urljoin, urlsplit

import aiohttp
from aiohttp.abc import AbstractResolver, ResolveResult
from jsonschema import Draft202012Validator, FormatChecker

from .config import ToolDefinitionConfig, ToolRegistryConfig
from .domain import SessionPolicy, ToolCall, ToolResult, ToolResultStatus
from .ports import EngineReadiness, ToolBatchPreflight

type SocketAddress = tuple[str, int] | tuple[str, int, int, int]
type AddressInfo = tuple[int, int, int, str, SocketAddress]
type AddressLookup = Callable[[str, int, int, int], Awaitable[Sequence[AddressInfo]]]

_USER_AGENT = "Harness/2.0 (+https://localhost.invalid/harness)"
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_HTML_CONTENT_TYPES = frozenset({"text/html", "application/xhtml+xml"})
_TEXT_CONTENT_TYPES = frozenset(
    {
        "application/json",
        "application/xml",
        "text/markdown",
        "text/plain",
        "text/xml",
    }
)
_SUPPRESSED_HTML_TAGS = frozenset({"aside", "footer", "nav", "script", "style"})
_BOUNDARY_HTML_TAGS = frozenset(
    {
        "article",
        "blockquote",
        "br",
        "div",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "li",
        "main",
        "p",
        "section",
        "tr",
    }
)


# The provider endpoint is a constructor default so a bench can serve a
# Brave-shaped response instead of the corpus depending on a real key.
BRAVE_SEARCH_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"


class EgressPolicyError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class EgressResolutionError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class ResolvedAddress:
    host: str
    port: int
    family: int
    proto: int


@dataclass(frozen=True, slots=True)
class ResolvedTarget:
    url: str
    hostname: str
    port: int
    addresses: tuple[ResolvedAddress, ...]


@dataclass(frozen=True, slots=True)
class HttpResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes


class HttpTransport(Protocol):
    async def request(
        self,
        target: ResolvedTarget,
        *,
        headers: Mapping[str, str],
        max_bytes: int,
        timeout_seconds: float,
    ) -> HttpResponse: ...


class ResponseByteLimitError(Exception):
    pass


class _PinnedResolver(AbstractResolver):
    def __init__(self, target: ResolvedTarget) -> None:
        self._target = target

    async def resolve(
        self,
        host: str,
        port: int = 0,
        family: socket.AddressFamily = socket.AF_INET,
    ) -> list[ResolveResult]:
        del family
        if host.casefold().rstrip(".") != self._target.hostname.casefold().rstrip(".") or (
            port and port != self._target.port
        ):
            raise OSError("Pinned resolver refused an unexpected destination.")
        return [
            ResolveResult(
                hostname=self._target.hostname,
                host=address.host,
                port=address.port,
                family=address.family,
                proto=address.proto,
                flags=0,
            )
            for address in self._target.addresses
        ]

    async def close(self) -> None:
        return None


class AiohttpHttpTransport:
    async def request(
        self,
        target: ResolvedTarget,
        *,
        headers: Mapping[str, str],
        max_bytes: int,
        timeout_seconds: float,
    ) -> HttpResponse:
        connector = aiohttp.TCPConnector(
            resolver=_PinnedResolver(target),
            use_dns_cache=False,
            force_close=True,
            limit=1,
        )
        timeout = aiohttp.ClientTimeout(total=timeout_seconds, connect=timeout_seconds)
        async with (
            aiohttp.ClientSession(
                connector=connector,
                cookie_jar=aiohttp.DummyCookieJar(),
                timeout=timeout,
                trust_env=False,
            ) as session,
            session.get(
                target.url,
                headers=headers,
                allow_redirects=False,
            ) as response,
        ):
            body = bytearray()
            async for chunk in response.content.iter_chunked(64 * 1024):
                body.extend(chunk)
                if len(body) > max_bytes:
                    raise ResponseByteLimitError
            return HttpResponse(
                status=response.status,
                headers=dict(response.headers),
                body=bytes(body),
            )


@dataclass(frozen=True, slots=True)
class BrowserPage:
    final_url: str
    content_type: str
    body: bytes


class BrowserCapability(Protocol):
    async def fetch(
        self,
        url: str,
        *,
        max_bytes: int,
        timeout_seconds: float,
    ) -> BrowserPage: ...


class BrowserEgressGuard(Protocol):
    async def readiness(self) -> EngineReadiness: ...


@dataclass(frozen=True, slots=True)
class _FetchArtifact:
    content: str
    content_type: str
    final_url: str
    producer: str


class _PreflightIssue(Exception):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


class GuardedResolver:
    def __init__(self, *, lookup: AddressLookup | None = None) -> None:
        self._lookup = lookup or _system_lookup
        self._cache: dict[tuple[str, int], tuple[ResolvedAddress, ...]] = {}

    async def resolve(
        self,
        host: str,
        port: int,
        family: int = socket.AF_UNSPEC,
    ) -> tuple[ResolvedAddress, ...]:
        key = (host.casefold().rstrip("."), port)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        try:
            records = await self._lookup(host, port, family, socket.SOCK_STREAM)
        except (OSError, UnicodeError) as error:
            raise EgressResolutionError("Host resolution failed.") from error
        if not records:
            raise EgressResolutionError("Host resolution returned no addresses.")

        resolved: list[ResolvedAddress] = []
        seen: set[tuple[str, int, int, int]] = set()
        for address_family, _socket_type, protocol, _canonical_name, socket_address in records:
            address = socket_address[0]
            try:
                parsed = ipaddress.ip_address(address)
            except ValueError as error:
                raise EgressResolutionError(
                    "Host resolution returned an invalid address."
                ) from error
            if _is_non_public(parsed):
                raise EgressPolicyError(
                    "non_public_address",
                    "The destination resolved to a non-public address.",
                )
            item = ResolvedAddress(
                host=parsed.compressed,
                port=port,
                family=address_family,
                proto=protocol,
            )
            identity = (item.host, item.port, item.family, item.proto)
            if identity not in seen:
                seen.add(identity)
                resolved.append(item)
        approved = tuple(resolved)
        self._cache[key] = approved
        return approved


class EgressGuard:
    def __init__(self, resolver: GuardedResolver | None = None) -> None:
        self._resolver = resolver or GuardedResolver()

    async def resolve(self, url: str) -> ResolvedTarget:
        parsed = self.validate_url(url)
        hostname = parsed.hostname
        if hostname is None:
            raise EgressPolicyError("url_host_required", "The URL must include a hostname.")
        try:
            explicit_port = parsed.port
        except ValueError as error:
            raise EgressPolicyError("invalid_url_port", "The URL port is invalid.") from error
        port = explicit_port or (443 if parsed.scheme.casefold() == "https" else 80)
        addresses = await self._resolver.resolve(hostname, port)
        return ResolvedTarget(
            url=url,
            hostname=hostname,
            port=port,
            addresses=addresses,
        )

    def validate_url(self, url: str) -> SplitResult:
        try:
            parsed = urlsplit(url)
        except ValueError as error:
            raise EgressPolicyError("invalid_url", "The URL is invalid.") from error
        if parsed.scheme.casefold() not in {"http", "https"}:
            raise EgressPolicyError(
                "disallowed_url_scheme",
                "Only HTTP and HTTPS URLs are allowed.",
            )
        if parsed.username is not None or parsed.password is not None:
            raise EgressPolicyError(
                "url_userinfo_not_allowed",
                "URL userinfo is not allowed.",
            )
        hostname = parsed.hostname
        if hostname is None:
            raise EgressPolicyError("url_host_required", "The URL must include a hostname.")
        try:
            port = parsed.port
        except ValueError as error:
            raise EgressPolicyError("invalid_url_port", "The URL port is invalid.") from error
        if port == 0:
            raise EgressPolicyError("invalid_url_port", "The URL port is invalid.")
        canonical_hostname = hostname.casefold().rstrip(".")
        if canonical_hostname == "localhost" or canonical_hostname.endswith(".localhost"):
            raise EgressPolicyError("localhost_not_allowed", "Localhost is not allowed.")
        try:
            literal = ipaddress.ip_address(canonical_hostname)
        except ValueError:
            literal = None
        if literal is not None and _is_non_public(literal):
            raise EgressPolicyError(
                "non_public_address",
                "The destination uses a non-public address.",
            )
        return parsed


class WebToolExecutor:
    def __init__(
        self,
        *,
        registry: ToolRegistryConfig,
        session_policy: SessionPolicy,
        egress_guard: EgressGuard | None = None,
        http_transport: HttpTransport | None = None,
        brave_api_key: str | None = None,
        search_endpoint: str = BRAVE_SEARCH_ENDPOINT,
        browser_capability: BrowserCapability | None = None,
        browser_egress_guard: BrowserEgressGuard | None = None,
        max_response_bytes: int = 2 * 1024 * 1024,
        max_redirects: int = 5,
        timeout_seconds: float = 15.0,
    ) -> None:
        if max_response_bytes < 1 or max_redirects < 0 or timeout_seconds <= 0:
            raise ValueError("Web limits must be positive.")
        self._registry = {
            definition.name: definition
            for definition in registry.model_tools
            if definition.name in {"web_fetch", "web_search"}
        }
        self._effective_grants = session_policy.effective_grants
        self._egress_guard = egress_guard or EgressGuard()
        self._http_transport = http_transport or AiohttpHttpTransport()
        self._brave_api_key = brave_api_key
        self._search_endpoint = search_endpoint
        self._browser_capability = browser_capability
        self._browser_egress_guard = browser_egress_guard
        self._max_response_bytes = max_response_bytes
        self._max_redirects = max_redirects
        self._timeout_seconds = timeout_seconds
        self._fetch_cache: dict[str, _FetchArtifact] = {}
        self._search_cache: dict[tuple[str, int], ToolResult] = {}

    async def preflight(self, calls: Sequence[ToolCall]) -> ToolBatchPreflight:
        seen_ids: set[str] = set()
        for call in calls:
            try:
                if not call.id or call.id in seen_ids:
                    raise _PreflightIssue(
                        "duplicate_tool_call_id",
                        "Tool call IDs must be non-empty and unique.",
                    )
                seen_ids.add(call.id)
                self._validate_call(call)
            except _PreflightIssue as issue:
                return ToolBatchPreflight(
                    allowed=False,
                    reason_code=issue.code,
                    detail=issue.detail,
                )
        return ToolBatchPreflight(allowed=True)

    async def execute(self, call: ToolCall) -> ToolResult:
        try:
            self._validate_call(call)
        except _PreflightIssue as issue:
            return _web_error(
                call,
                ToolResultStatus.BLOCKED,
                issue.code,
                issue.detail,
                retryable=False,
            )
        if call.name == "web_fetch":
            return await self._web_fetch(call)
        return await self._web_search(call)

    async def _web_fetch(self, call: ToolCall) -> ToolResult:
        url = cast(str, call.arguments["url"])
        limit = cast(int, call.arguments.get("limit", 12000))
        cached = self._fetch_cache.get(url)
        if cached is not None:
            return _fetch_result(call, cached, limit=limit, cache_hit=True)

        current_url = url
        for redirect_count in range(self._max_redirects + 1):
            try:
                async with asyncio.timeout(self._timeout_seconds):
                    target = await self._egress_guard.resolve(current_url)
            except EgressPolicyError as error:
                return _web_error(
                    call,
                    ToolResultStatus.BLOCKED,
                    error.code,
                    str(error),
                    retryable=False,
                    producer="web_fetch",
                    final_url=current_url,
                )
            except EgressResolutionError:
                return _web_error(
                    call,
                    ToolResultStatus.FAILED,
                    "dns_resolution_failed",
                    "The destination hostname could not be resolved.",
                    retryable=True,
                    producer="web_fetch",
                    final_url=current_url,
                )
            except TimeoutError:
                return _web_error(
                    call,
                    ToolResultStatus.FAILED,
                    "dns_resolution_timeout",
                    "The destination hostname resolution exceeded the time limit.",
                    retryable=True,
                    producer="web_fetch",
                    final_url=current_url,
                )
            try:
                async with asyncio.timeout(self._timeout_seconds):
                    response = await self._http_transport.request(
                        target,
                        headers={
                            "Accept": "text/html, text/plain;q=0.9, */*;q=0.1",
                            "User-Agent": _USER_AGENT,
                        },
                        max_bytes=self._max_response_bytes,
                        timeout_seconds=self._timeout_seconds,
                    )
            except ResponseByteLimitError:
                return _web_error(
                    call,
                    ToolResultStatus.BLOCKED,
                    "response_byte_limit_exceeded",
                    "The web response exceeded the byte limit.",
                    retryable=False,
                    producer="web_fetch",
                    final_url=current_url,
                )
            except TimeoutError:
                return _web_error(
                    call,
                    ToolResultStatus.FAILED,
                    "response_timeout",
                    "The web response exceeded the time limit.",
                    retryable=True,
                    producer="web_fetch",
                    final_url=current_url,
                )
            except (OSError, aiohttp.ClientError):
                return _web_error(
                    call,
                    ToolResultStatus.FAILED,
                    "transport_unavailable",
                    "The web transport was unavailable.",
                    retryable=True,
                    producer="web_fetch",
                    final_url=current_url,
                )

            if len(response.body) > self._max_response_bytes:
                return _web_error(
                    call,
                    ToolResultStatus.BLOCKED,
                    "response_byte_limit_exceeded",
                    "The web response exceeded the byte limit.",
                    retryable=False,
                    producer="web_fetch",
                    final_url=current_url,
                )
            if response.status in _REDIRECT_STATUSES:
                location = _header(response.headers, "location")
                if location is None:
                    return _web_error(
                        call,
                        ToolResultStatus.FAILED,
                        "invalid_redirect",
                        "The redirect response did not include a Location header.",
                        retryable=False,
                        producer="web_fetch",
                        final_url=current_url,
                    )
                if redirect_count >= self._max_redirects:
                    return _web_error(
                        call,
                        ToolResultStatus.BLOCKED,
                        "redirect_limit_exceeded",
                        "The web response exceeded the redirect limit.",
                        retryable=False,
                        producer="web_fetch",
                        final_url=current_url,
                    )
                current_url = urljoin(current_url, location)
                continue
            if response.status in {401, 403}:
                return await self._browser_escalation(call, current_url, limit=limit)
            if response.status < 200 or response.status >= 300:
                return _http_status_error(call, response.status, final_url=current_url)

            artifact_or_error = _response_artifact(response, current_url, producer="web_fetch")
            if isinstance(artifact_or_error, tuple):
                code, message = artifact_or_error
                if code == "empty_extraction":
                    # The declared escalation symptom: HTTP returned a page whose readable
                    # extraction is below the calibrated threshold, which is what a
                    # JavaScript-rendered page looks like to an HTTP client.
                    return await self._browser_escalation(call, current_url, limit=limit)
                return _web_error(
                    call,
                    ToolResultStatus.FAILED,
                    code,
                    message,
                    retryable=False,
                    producer="web_fetch",
                    final_url=current_url,
                )
            self._fetch_cache[url] = artifact_or_error
            return _fetch_result(call, artifact_or_error, limit=limit, cache_hit=False)

        raise AssertionError("redirect loop must return before exhaustion")

    async def _browser_escalation(
        self,
        call: ToolCall,
        url: str,
        *,
        limit: int,
    ) -> ToolResult:
        if self._browser_capability is None or self._browser_egress_guard is None:
            return _browser_unavailable(call, url)
        try:
            async with asyncio.timeout(self._timeout_seconds):
                readiness = await self._browser_egress_guard.readiness()
        except (OSError, TimeoutError):
            return _browser_unavailable(call, url)
        if not readiness.ready:
            return _browser_unavailable(call, url)
        try:
            async with asyncio.timeout(self._timeout_seconds):
                page = await self._browser_capability.fetch(
                    url,
                    max_bytes=self._max_response_bytes,
                    timeout_seconds=self._timeout_seconds,
                )
        except ResponseByteLimitError:
            return _web_error(
                call,
                ToolResultStatus.BLOCKED,
                "response_byte_limit_exceeded",
                "The browser response exceeded the byte limit.",
                retryable=False,
                producer="browser",
                final_url=url,
            )
        except (OSError, TimeoutError):
            return _web_error(
                call,
                ToolResultStatus.FAILED,
                "browser_escalation_failed",
                "The guarded browser escalation failed.",
                retryable=True,
                producer="browser",
                final_url=url,
            )
        if len(page.body) > self._max_response_bytes:
            return _web_error(
                call,
                ToolResultStatus.BLOCKED,
                "response_byte_limit_exceeded",
                "The browser response exceeded the byte limit.",
                retryable=False,
                producer="browser",
                final_url=page.final_url,
            )
        try:
            async with asyncio.timeout(self._timeout_seconds):
                await self._egress_guard.resolve(page.final_url)
        except EgressPolicyError as error:
            return _web_error(
                call,
                ToolResultStatus.BLOCKED,
                error.code,
                str(error),
                retryable=False,
                producer="browser",
                final_url=page.final_url,
            )
        except EgressResolutionError:
            return _web_error(
                call,
                ToolResultStatus.FAILED,
                "dns_resolution_failed",
                "The browser final URL could not be resolved.",
                retryable=True,
                producer="browser",
                final_url=page.final_url,
            )
        except TimeoutError:
            return _web_error(
                call,
                ToolResultStatus.FAILED,
                "dns_resolution_timeout",
                "The browser final URL resolution exceeded the time limit.",
                retryable=True,
                producer="browser",
                final_url=page.final_url,
            )
        response = HttpResponse(
            status=200,
            headers={"Content-Type": page.content_type},
            body=page.body,
        )
        artifact_or_error = _response_artifact(response, page.final_url, producer="browser")
        if isinstance(artifact_or_error, tuple):
            code, message = artifact_or_error
            return _web_error(
                call,
                ToolResultStatus.FAILED,
                code,
                message,
                retryable=False,
                producer="browser",
                final_url=page.final_url,
            )
        source_url = cast(str, call.arguments["url"])
        self._fetch_cache[source_url] = artifact_or_error
        return _fetch_result(call, artifact_or_error, limit=limit, cache_hit=False)

    async def _web_search(self, call: ToolCall) -> ToolResult:
        query = _normalized_query(cast(str, call.arguments["query"]))
        limit = cast(int, call.arguments.get("limit", 8))
        cache_key = (query.casefold(), limit)
        cached = self._search_cache.get(cache_key)
        if cached is not None:
            return replace(
                cached,
                tool_call_id=call.id,
                meta={**cached.meta, "cache_hit": True},
            )
        if not self._brave_api_key:
            return _provider_error(
                call,
                "provider_auth_unavailable",
                "Brave Search credentials are not available.",
                retryable=False,
            )

        request_url = self._search_endpoint + "?" + urlencode({"q": query, "count": limit})
        try:
            async with asyncio.timeout(self._timeout_seconds):
                target = await self._egress_guard.resolve(request_url)
        except EgressPolicyError as error:
            return _web_error(
                call,
                ToolResultStatus.BLOCKED,
                error.code,
                str(error),
                retryable=False,
                producer="brave_search",
            )
        except EgressResolutionError:
            return _provider_error(
                call,
                "provider_unavailable",
                "Brave Search could not be reached.",
                retryable=True,
            )
        except TimeoutError:
            return _provider_error(
                call,
                "provider_unavailable",
                "Brave Search hostname resolution timed out.",
                retryable=True,
            )
        try:
            async with asyncio.timeout(self._timeout_seconds):
                response = await self._http_transport.request(
                    target,
                    headers={
                        "Accept": "application/json",
                        "User-Agent": _USER_AGENT,
                        "X-Subscription-Token": self._brave_api_key,
                    },
                    max_bytes=self._max_response_bytes,
                    timeout_seconds=self._timeout_seconds,
                )
        except ResponseByteLimitError:
            return _web_error(
                call,
                ToolResultStatus.BLOCKED,
                "response_byte_limit_exceeded",
                "The provider response exceeded the byte limit.",
                retryable=False,
                producer="brave_search",
            )
        except (OSError, TimeoutError, aiohttp.ClientError):
            return _provider_error(
                call,
                "provider_unavailable",
                "Brave Search could not be reached.",
                retryable=True,
            )
        if len(response.body) > self._max_response_bytes:
            return _web_error(
                call,
                ToolResultStatus.BLOCKED,
                "response_byte_limit_exceeded",
                "The provider response exceeded the byte limit.",
                retryable=False,
                producer="brave_search",
            )
        if response.status in {401, 403}:
            return _provider_error(
                call,
                "provider_auth_failed",
                "Brave Search rejected its credentials.",
                retryable=False,
            )
        if response.status == 429:
            return _provider_error(
                call,
                "provider_rate_limited",
                "Brave Search rate limited the request.",
                retryable=True,
            )
        if response.status < 200 or response.status >= 300:
            return _provider_error(
                call,
                "provider_unavailable" if response.status >= 500 else "provider_error",
                f"Brave Search returned HTTP {response.status}.",
                retryable=response.status >= 500 or response.status == 408,
            )
        content_type = _header(response.headers, "content-type")
        if (
            content_type is None
            or content_type.partition(";")[0].strip().casefold() != "application/json"
        ):
            return _provider_error(
                call,
                "provider_invalid_response",
                "Brave Search returned an unsupported Content-Type.",
                retryable=True,
            )
        try:
            payload: object = json.loads(response.body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return _provider_error(
                call,
                "provider_invalid_response",
                "Brave Search returned invalid JSON.",
                retryable=True,
            )
        parsed = _parse_brave_results(payload, limit=limit)
        if parsed is None:
            return _provider_error(
                call,
                "provider_invalid_response",
                "Brave Search returned an invalid result structure.",
                retryable=True,
            )
        results, truncated = parsed
        meta: dict[str, Any] = {
            "producer": "brave_search",
            "truncated": truncated,
            "taints": ["UntrustedWebTaint"],
            "engine": "brave",
            "cache_hit": False,
        }
        if truncated:
            meta["continuation"] = {"offset": limit}
        result = ToolResult(
            tool_call_id=call.id,
            status=ToolResultStatus.SUCCESS if results else ToolResultStatus.EMPTY,
            retryable=False,
            data={"query": query, "results": results},
            error=None,
            meta=meta,
        )
        self._search_cache[cache_key] = result
        return result

    def _validate_call(self, call: ToolCall) -> ToolDefinitionConfig:
        definition = self._registry.get(call.name)
        if definition is None:
            raise _PreflightIssue("unknown_tool", "Unknown web tool.")
        if definition.status != "enabled":
            raise _PreflightIssue("tool_not_enabled", "The requested tool is not enabled.")
        validator = Draft202012Validator(
            cast(Mapping[str, Any], definition.parameters),
            format_checker=FormatChecker(),
        )
        errors = sorted(
            validator.iter_errors(call.arguments),  # pyright: ignore[reportUnknownMemberType]
            key=lambda error: list(error.path),
        )
        if errors:
            raise _PreflightIssue("invalid_tool_arguments", errors[0].message)
        if call.name == "web_search" and not _normalized_query(cast(str, call.arguments["query"])):
            raise _PreflightIssue("invalid_tool_arguments", "The search query must not be blank.")
        if call.name == "web_fetch":
            try:
                self._egress_guard.validate_url(cast(str, call.arguments["url"]))
            except EgressPolicyError as error:
                raise _PreflightIssue(error.code, str(error)) from error
        if "WebAccessGrant" not in self._effective_grants:
            raise _PreflightIssue(
                "web_access_grant_required",
                "The WebAccessGrant is required for this effect.",
            )
        return definition


async def _system_lookup(
    host: str,
    port: int,
    family: int,
    socket_type: int,
) -> Sequence[AddressInfo]:
    loop = asyncio.get_running_loop()
    records = await loop.getaddrinfo(host, port, family=family, type=socket_type)
    converted: list[AddressInfo] = []
    for item in records:
        socket_address = item[4]
        if not isinstance(socket_address[0], str):
            raise EgressResolutionError("Host resolution returned an invalid address.")
        converted.append(
            (
                int(item[0]),
                int(item[1]),
                item[2],
                item[3],
                cast(SocketAddress, socket_address),
            )
        )
    return converted


def _is_non_public(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    # `is_global` already subsumes loopback, private, link-local, reserved and
    # unspecified in both families; multicast is the one range it calls global.
    return address.is_multicast or not address.is_global


class _ReadableHTMLParser(HTMLParser):
    def __init__(self, base_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self._base_url = base_url
        self._parts: list[str] = []
        self._suppressed_depth = 0
        self._anchors: list[str | None] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lowered = tag.casefold()
        if self._suppressed_depth:
            if lowered in _SUPPRESSED_HTML_TAGS:
                self._suppressed_depth += 1
            return
        if lowered in _SUPPRESSED_HTML_TAGS:
            self._suppressed_depth = 1
            return
        if lowered in _BOUNDARY_HTML_TAGS:
            self._parts.append("\n")
        if lowered == "a":
            href = next((value for name, value in attrs if name.casefold() == "href"), None)
            self._anchors.append(_useful_link(self._base_url, href))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.casefold()
        if self._suppressed_depth:
            if lowered in _SUPPRESSED_HTML_TAGS:
                self._suppressed_depth -= 1
            return
        if lowered == "a" and self._anchors:
            href = self._anchors.pop()
            if href is not None:
                self._parts.append(f" ({href})")
        if lowered in _BOUNDARY_HTML_TAGS:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._suppressed_depth:
            self._parts.append(data)

    def content(self) -> str:
        lines = (" ".join(line.split()) for line in "".join(self._parts).splitlines())
        return "\n".join(line for line in lines if line).strip()


def _response_artifact(
    response: HttpResponse,
    final_url: str,
    *,
    producer: str,
) -> _FetchArtifact | tuple[str, str]:
    content_type_header = _header(response.headers, "content-type")
    if content_type_header is None:
        return "content_type_required", "The web response did not include a Content-Type header."
    content_type = content_type_header.partition(";")[0].strip().casefold()
    if content_type not in _HTML_CONTENT_TYPES | _TEXT_CONTENT_TYPES:
        return "unsupported_content_type", "The web response Content-Type is not supported."
    charset_match = re.search(r"charset\s*=\s*[\"']?([^;\s\"']+)", content_type_header, re.I)
    charset = charset_match.group(1) if charset_match is not None else "utf-8"
    try:
        decoded = response.body.decode(charset, errors="replace")
    except LookupError:
        return "unsupported_charset", "The web response charset is not supported."
    if content_type in _HTML_CONTENT_TYPES:
        parser = _ReadableHTMLParser(final_url)
        parser.feed(decoded)
        parser.close()
        content = parser.content()
    else:
        content = decoded.strip()
    if len(content) < 120:
        return "empty_extraction", "The web response did not contain enough readable content."
    return _FetchArtifact(
        content=content,
        content_type=content_type,
        final_url=final_url,
        producer=producer,
    )


def _fetch_result(
    call: ToolCall,
    artifact: _FetchArtifact,
    *,
    limit: int,
    cache_hit: bool,
) -> ToolResult:
    truncated = len(artifact.content) > limit
    meta: dict[str, Any] = {
        "producer": artifact.producer,
        "truncated": truncated,
        "taints": ["UntrustedWebTaint"],
        "final_url": artifact.final_url,
        "cache_hit": cache_hit,
    }
    if truncated:
        meta["continuation"] = {"offset": limit}
    return ToolResult(
        tool_call_id=call.id,
        status=ToolResultStatus.SUCCESS,
        retryable=False,
        data={
            "url": cast(str, call.arguments["url"]),
            "final_url": artifact.final_url,
            "content_type": artifact.content_type,
            "content": artifact.content[:limit],
        },
        error=None,
        meta=meta,
    )


def _useful_link(base_url: str, href: str | None) -> str | None:
    if href is None:
        return None
    absolute = urljoin(base_url, href.strip())
    if urlsplit(absolute).scheme.casefold() not in {"http", "https"}:
        return None
    return absolute


def _header(headers: Mapping[str, str], name: str) -> str | None:
    expected = name.casefold()
    return next((value for key, value in headers.items() if key.casefold() == expected), None)


def _http_status_error(call: ToolCall, status: int, *, final_url: str) -> ToolResult:
    retryable = status == 429 or status == 408 or status >= 500
    code = "http_rate_limited" if status == 429 else "http_upstream_error"
    return _web_error(
        call,
        ToolResultStatus.FAILED,
        code,
        f"The web server returned HTTP {status}.",
        retryable=retryable,
        producer="web_fetch",
        final_url=final_url,
    )


def _browser_unavailable(call: ToolCall, final_url: str) -> ToolResult:
    return _web_error(
        call,
        ToolResultStatus.FAILED,
        "browser_escalation_unavailable",
        "Guarded browser escalation is not available.",
        retryable=False,
        producer="web_fetch",
        final_url=final_url,
    )


def _normalized_query(query: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", query).split())


def _parse_brave_results(
    payload: object,
    *,
    limit: int,
) -> tuple[list[dict[str, str]], bool] | None:
    if not isinstance(payload, Mapping):
        return None
    payload_mapping = cast(Mapping[str, object], payload)
    web = payload_mapping.get("web")
    if web is None:
        raw_results: object = []
    elif isinstance(web, Mapping):
        raw_results = cast(Mapping[str, object], web).get("results", [])
    else:
        return None
    if not isinstance(raw_results, list):
        return None
    raw_result_items = cast(list[object], raw_results)
    results: list[dict[str, str]] = []
    for item in raw_result_items[:limit]:
        if not isinstance(item, Mapping):
            return None
        item_mapping = cast(Mapping[str, object], item)
        title = item_mapping.get("title")
        url = item_mapping.get("url")
        description = item_mapping.get("description", "")
        if (
            not isinstance(title, str)
            or not isinstance(url, str)
            or not isinstance(description, str)
        ):
            return None
        results.append({"title": title, "url": url, "description": description})
    query_info = payload_mapping.get("query")
    more_available = (
        isinstance(query_info, Mapping)
        and cast(Mapping[str, object], query_info).get("more_results_available") is True
    )
    return results, len(raw_result_items) > limit or more_available


def _provider_error(
    call: ToolCall,
    code: str,
    message: str,
    *,
    retryable: bool,
) -> ToolResult:
    result = _web_error(
        call,
        ToolResultStatus.FAILED,
        code,
        message,
        retryable=retryable,
        producer="brave_search",
    )
    return replace(result, meta={**result.meta, "engine": "brave"})


def _web_error(
    call: ToolCall,
    status: ToolResultStatus,
    code: str,
    message: str,
    *,
    retryable: bool,
    producer: str = "web",
    final_url: str | None = None,
) -> ToolResult:
    meta: dict[str, Any] = {
        "producer": producer,
        "truncated": False,
        "taints": ["UntrustedWebTaint"],
    }
    if final_url is not None:
        meta["final_url"] = final_url
    return ToolResult(
        tool_call_id=call.id,
        status=status,
        retryable=retryable,
        data=None,
        error={"code": code, "message": message},
        meta=meta,
    )
