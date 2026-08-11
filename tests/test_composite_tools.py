import asyncio
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path

from harness import (
    CompositeToolExecutor,
    ConfirmationPreview,
    Grant,
    HttpResponse,
    RegistryToolExecutor,
    ResolvedTarget,
    SessionPolicy,
    ToolBatchPreflight,
    ToolCall,
    ToolExecutor,
    ToolResult,
    ToolResultStatus,
    WebToolExecutor,
    load_config,
)


class RecordingExecutor:
    def __init__(self, delegate: ToolExecutor) -> None:
        self.delegate = delegate
        self.preflight_batches: list[tuple[ToolCall, ...]] = []

    async def preflight(self, calls: Sequence[ToolCall]) -> ToolBatchPreflight:
        self.preflight_batches.append(tuple(calls))
        return await self.delegate.preflight(calls)

    async def execute(self, call: ToolCall) -> ToolResult:
        return await self.delegate.execute(call)

    async def preview(self, call: ToolCall) -> ConfirmationPreview | None:
        return await self.delegate.preview(call)


class NoEffectHttpTransport:
    def __init__(self) -> None:
        self.requests: list[ResolvedTarget] = []

    async def request(
        self,
        target: ResolvedTarget,
        *,
        headers: Mapping[str, str],
        max_bytes: int,
        timeout_seconds: float,
    ) -> HttpResponse:
        del headers, max_bytes, timeout_seconds
        self.requests.append(target)
        raise AssertionError("HTTP must not execute for a denied batch")


def policy(*permissions: str) -> SessionPolicy:
    now = datetime.now(UTC)
    return SessionPolicy(
        conversation_id="conversation-1",
        grants=tuple(
            Grant(
                id=f"grant-{permission}",
                conversation_id="conversation-1",
                permission=permission,
                scope="test",
                granted_at=now,
            )
            for permission in permissions
        ),
    )


class OrderedChildExecutor:
    def __init__(self, name: str, events: list[str]) -> None:
        self.name = name
        self.events = events
        self.preflight_batches: list[tuple[ToolCall, ...]] = []

    async def preflight(self, calls: Sequence[ToolCall]) -> ToolBatchPreflight:
        self.preflight_batches.append(tuple(calls))
        return ToolBatchPreflight(allowed=True)

    async def preview(self, call: ToolCall) -> ConfirmationPreview | None:
        del call
        return None

    async def execute(self, call: ToolCall) -> ToolResult:
        self.events.append(call.id)
        return ToolResult(
            tool_call_id=call.id,
            status=ToolResultStatus.SUCCESS,
            retryable=False,
            data={"executor": self.name},
            error=None,
            meta={"producer": self.name, "truncated": False, "taints": []},
        )


def test_mixed_batch_preflights_all_children_and_has_no_effect_when_web_is_denied(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        config = load_config()
        local = RecordingExecutor(
            RegistryToolExecutor(
                registry=config.tool_registry,
                workspace_root=tmp_path,
                session_policy=policy("WorkspaceRootGrant", "WriteGrant"),
            )
        )
        web_transport = NoEffectHttpTransport()
        web = RecordingExecutor(
            WebToolExecutor(
                registry=config.tool_registry,
                session_policy=policy(),
                http_transport=web_transport,
                brave_api_key="not-used",
            )
        )
        composite = CompositeToolExecutor(
            routes={
                "write_file": local,
                "web_fetch": web,
            }
        )
        calls = (
            ToolCall(
                id="write",
                name="write_file",
                arguments={"file_path": "effect.txt", "content": "must not happen"},
            ),
            ToolCall(
                id="fetch",
                name="web_fetch",
                arguments={"url": "https://example.com"},
            ),
        )

        preflight = await composite.preflight(calls)
        denied_execute = await composite.execute(calls[0])

        assert preflight.allowed is False
        assert preflight.reason_code == "web_access_grant_required"
        assert local.preflight_batches == [(calls[0],)]
        assert web.preflight_batches == [(calls[1],)]
        assert denied_execute.status.value == "blocked"
        assert denied_execute.error is not None
        assert denied_execute.error["code"] == "batch_not_preflighted"
        assert not (tmp_path / "effect.txt").exists()
        assert web_transport.requests == []

    asyncio.run(scenario())


def test_composite_routes_subsequences_and_enforces_original_execution_order() -> None:
    async def scenario() -> None:
        events: list[str] = []
        local = OrderedChildExecutor("local", events)
        web = OrderedChildExecutor("web", events)
        composite = CompositeToolExecutor(
            routes={"read_file": local, "web_fetch": web, "grep_search": local}
        )
        calls = (
            ToolCall(id="first", name="read_file", arguments={}),
            ToolCall(id="second", name="web_fetch", arguments={}),
            ToolCall(id="third", name="grep_search", arguments={}),
        )

        assert (await composite.preflight(calls)).allowed is True
        out_of_order = await composite.execute(calls[1])
        assert out_of_order.status is ToolResultStatus.BLOCKED
        assert out_of_order.error is not None
        assert out_of_order.error["code"] == "batch_execution_order_violation"
        assert events == []

        results = [await composite.execute(call) for call in calls]
        assert [result.tool_call_id for result in results] == ["first", "second", "third"]
        assert events == ["first", "second", "third"]
        assert local.preflight_batches == [(calls[0], calls[2])]
        assert web.preflight_batches == [(calls[1],)]

    asyncio.run(scenario())
