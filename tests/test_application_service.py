import asyncio
import json
from collections.abc import Sequence
from pathlib import Path
from typing import cast
from unittest.mock import patch

from harness import (
    ApplicationService,
    BenchmarkLease,
    ConversationStore,
    EngineReadiness,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ObservabilityStore,
    TerminalOutcomeKind,
    ToolSchema,
    TurnStatus,
    load_config,
)


class FakeEstimator:
    validated = True
    readiness = EngineReadiness(ready=True)

    def estimate(self, messages: Sequence[ModelMessage], tools: Sequence[ToolSchema]) -> int:
        return len(messages) + len(tools)


class CapturingRuntime:
    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []

    async def verify_profile(self) -> EngineReadiness:
        return EngineReadiness(ready=True)

    async def generate(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        return ModelResponse(content="ok")

    async def aclose(self) -> None:
        return None


class UnreadyRuntime(CapturingRuntime):
    async def verify_profile(self) -> EngineReadiness:
        return EngineReadiness(ready=False, reason_code="model_digest_mismatch")


async def _wait_until_idle(service: ApplicationService, conversation_id: str) -> None:
    for _ in range(300):
        snapshot = await service.chat_snapshot(conversation_id)
        if snapshot["active_turn"] is None and not snapshot["pending_requests"]:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("conversation worker did not become idle")


def test_service_exposes_effective_tool_schemas_without_disclosing_brave_key(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        runtime = CapturingRuntime()
        secret = "brave-secret-that-must-not-leak"
        service = ApplicationService(
            store=ConversationStore(tmp_path / "conversations.sqlite3"),
            observability_store=ObservabilityStore(tmp_path / "observability.sqlite3"),
            config=load_config(),
            runtime=runtime,
            estimator=FakeEstimator(),
            allowed_workspace_roots=(workspace,),
            brave_api_key=secret,
        )
        await service.initialize()
        try:
            conversation = await service.create_conversation(str(workspace))

            with patch(
                "harness.conversation_store.secrets.randbits",
                side_effect=(101, 202, 303),
            ):
                await service.enqueue_request(conversation.id, "without effect grants")
                await _wait_until_idle(service, conversation.id)

                write_grant = await service.grant(conversation.id, "WriteGrant")
                web_grant = await service.grant(conversation.id, "WebAccessGrant")
                await service.enqueue_request(conversation.id, "with effect grants")
                await _wait_until_idle(service, conversation.id)

                await service.revoke_grant(conversation.id, write_grant.id)
                await service.revoke_grant(conversation.id, web_grant.id)
                await service.enqueue_request(conversation.id, "after revocation")
                await _wait_until_idle(service, conversation.id)

            offered = [{schema.name for schema in request.tools} for request in runtime.requests]
            assert offered == [
                {"read_file", "list_directory", "glob", "grep_search"},
                {
                    "read_file",
                    "write_file",
                    "edit",
                    "list_directory",
                    "glob",
                    "grep_search",
                    "web_fetch",
                    "web_search",
                },
                {"read_file", "list_directory", "glob", "grep_search"},
            ]

            snapshot_text = repr(await service.chat_snapshot(conversation.id))
            events_text = json.dumps(
                [event.payload for event in await service.observability_store.list_events()]
            )
            assert secret not in snapshot_text
            assert secret not in events_text

            turns = await service.store.list_turns(conversation.id)
            step_events = [
                event
                for event in await service.observability_store.list_events()
                if event.event_type == "agent.step_started"
            ]
            assert [turn.base_seed for turn in turns] == [101, 202, 303]
            assert [event.payload["base_seed"] for event in step_events] == [
                turn.base_seed for turn in turns
            ]
            assert [event.payload["seed"] for event in step_events] == [
                (await service.store.list_agent_steps(turn.id))[0].seed for turn in turns
            ]
        finally:
            await service.shutdown()

    asyncio.run(scenario())


def test_web_search_schema_requires_brave_capability(tmp_path: Path) -> None:
    async def scenario() -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        runtime = CapturingRuntime()
        service = ApplicationService(
            store=ConversationStore(tmp_path / "conversations.sqlite3"),
            observability_store=ObservabilityStore(tmp_path / "observability.sqlite3"),
            config=load_config(),
            runtime=runtime,
            estimator=FakeEstimator(),
            allowed_workspace_roots=(workspace,),
        )
        await service.initialize()
        try:
            conversation = await service.create_conversation(str(workspace))
            await service.grant(conversation.id, "WebAccessGrant")
            await service.enqueue_request(conversation.id, "use available web tools")
            await _wait_until_idle(service, conversation.id)

            offered = {schema.name for schema in runtime.requests[0].tools}
            assert "web_fetch" in offered
            assert "web_search" not in offered
        finally:
            await service.shutdown()

    asyncio.run(scenario())


def test_initialize_abandons_stale_active_turn_before_recovering_pending_queue(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        store = ConversationStore(tmp_path / "conversations.sqlite3")
        await store.initialize()
        revision = await store.create_workspace(str(workspace.resolve()))
        conversation = await store.create_conversation(revision.workspace_id)
        await store.enqueue_request(conversation.id, "stale in-flight request")
        stale = await store.start_next_turn(conversation.id, base_seed=1)
        assert stale is not None
        await store.enqueue_request(conversation.id, "recover me")

        runtime = CapturingRuntime()
        service = ApplicationService(
            store=store,
            observability_store=ObservabilityStore(tmp_path / "observability.sqlite3"),
            config=load_config(),
            runtime=runtime,
            estimator=FakeEstimator(),
            allowed_workspace_roots=(workspace,),
        )
        await service.initialize()
        try:
            await _wait_until_idle(service, conversation.id)
            turns = await store.list_turns(conversation.id)

            assert len(runtime.requests) == 1
            assert [turn.status for turn in turns] == [
                TurnStatus.FINISHED,
                TurnStatus.FINISHED,
            ]
            assert turns[0].terminal_outcome is not None
            assert turns[0].terminal_outcome.kind is TerminalOutcomeKind.ABANDONED
            assert turns[0].terminal_outcome.reason_code == "recovered_stale_active_turn"
            assert turns[1].terminal_outcome is not None
            assert turns[1].terminal_outcome.kind is TerminalOutcomeKind.COMPLETED
        finally:
            await service.shutdown()

    asyncio.run(scenario())


def test_unready_runtime_profile_fails_closed_without_model_generation(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        runtime = UnreadyRuntime()
        service = ApplicationService(
            store=ConversationStore(tmp_path / "conversations.sqlite3"),
            observability_store=ObservabilityStore(tmp_path / "observability.sqlite3"),
            config=load_config(),
            runtime=runtime,
            estimator=FakeEstimator(),
            allowed_workspace_roots=(workspace,),
        )
        await service.initialize()
        try:
            assert service.readiness == EngineReadiness(
                ready=False, reason_code="model_digest_mismatch"
            )
            conversation = await service.create_conversation(str(workspace))
            await service.enqueue_request(conversation.id, "must not reach runtime")
            await _wait_until_idle(service, conversation.id)

            turns = await service.store.list_turns(conversation.id)
            assert runtime.requests == []
            assert turns[0].terminal_outcome is not None
            assert turns[0].terminal_outcome.kind is TerminalOutcomeKind.BLOCKED
            assert turns[0].terminal_outcome.reason_code == "model_digest_mismatch"
        finally:
            await service.shutdown()

    asyncio.run(scenario())


def test_benchmark_lease_waits_for_active_turn_and_keeps_new_chat_queued(
    tmp_path: Path,
) -> None:
    class GatedRuntime(CapturingRuntime):
        def __init__(self) -> None:
            super().__init__()
            self.entered = asyncio.Event()
            self.release = asyncio.Event()

        async def generate(self, request: ModelRequest) -> ModelResponse:
            self.requests.append(request)
            if len(self.requests) == 1:
                self.entered.set()
                await self.release.wait()
            return ModelResponse(content="ok")

    async def scenario() -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        lease = BenchmarkLease()
        runtime = GatedRuntime()
        service = ApplicationService(
            store=ConversationStore(tmp_path / "conversations.sqlite3"),
            observability_store=ObservabilityStore(tmp_path / "observability.sqlite3"),
            config=load_config(),
            runtime=runtime,
            estimator=FakeEstimator(),
            allowed_workspace_roots=(workspace,),
            benchmark_lease=lease,
        )
        await service.initialize()
        try:
            active = await service.create_conversation(str(workspace), name="active")
            queued = await service.create_conversation(str(workspace), name="queued")
            await service.enqueue_request(active.id, "active turn")
            await asyncio.wait_for(runtime.entered.wait(), timeout=1)

            benchmark = asyncio.create_task(lease.acquire("benchmark-run"))
            await asyncio.sleep(0)
            await service.enqueue_request(queued.id, "must remain queued")
            await asyncio.sleep(0.02)

            queued_snapshot = await service.chat_snapshot(queued.id)
            assert queued_snapshot["active_turn"] is None
            assert len(cast(list[object], queued_snapshot["pending_requests"])) == 1
            assert not benchmark.done()

            runtime.release.set()
            await asyncio.wait_for(benchmark, timeout=1)
            await _wait_until_idle(service, active.id)
            assert len(runtime.requests) == 1
            queued_after_acquire = await service.chat_snapshot(queued.id)
            assert len(cast(list[object], queued_after_acquire["pending_requests"])) == 1

            await lease.release("benchmark-run")
            await _wait_until_idle(service, queued.id)
            assert len(runtime.requests) == 2
        finally:
            await service.shutdown()

    asyncio.run(scenario())
