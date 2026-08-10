import asyncio
import json
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from harness import (
    AgentEngine,
    AgentEvent,
    AgentEventKind,
    CanonicalHistoryEntryKind,
    ContextBuilder,
    ConversationStore,
    Grant,
    MalformedModelResponseError,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    RegistryToolExecutor,
    SessionPolicy,
    TerminalOutcomeKind,
    ToolBatchPreflight,
    ToolCall,
    ToolExecutor,
    ToolExecutorFactory,
    ToolResult,
    ToolResultStatus,
    ToolSchema,
    load_config,
)


class FakeEstimator:
    validated = True

    def estimate(
        self, messages: Sequence[ModelMessage], tools: Sequence[ToolSchema]
    ) -> int:
        return len(messages) + len(tools)


class UnvalidatedEstimator(FakeEstimator):
    validated = False


class FakeRuntime:
    def __init__(self, responses: Sequence[ModelResponse | Exception]) -> None:
        self.responses = list(responses)
        self.requests: list[ModelRequest] = []

    async def generate(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class FakeToolExecutor:
    def __init__(self, preflight: ToolBatchPreflight | None = None) -> None:
        self.preflight_result = preflight or ToolBatchPreflight(allowed=True)
        self.preflight_batches: list[tuple[ToolCall, ...]] = []
        self.executed: list[ToolCall] = []

    async def preflight(self, calls: Sequence[ToolCall]) -> ToolBatchPreflight:
        self.preflight_batches.append(tuple(calls))
        return self.preflight_result

    async def execute(self, call: ToolCall) -> ToolResult:
        self.executed.append(call)
        return ToolResult(
            tool_call_id=call.id,
            status=ToolResultStatus.SUCCESS,
            retryable=False,
            data={"value": call.arguments.get("value")},
            error=None,
            meta={"producer": "fake", "truncated": False, "taints": []},
        )


class FakeEventSink:
    def __init__(self) -> None:
        self.events: list[AgentEvent] = []

    async def emit(self, event: AgentEvent) -> None:
        self.events.append(event)


async def conversation_store(tmp_path: Path) -> tuple[ConversationStore, str]:
    store = ConversationStore(tmp_path / "conversations.sqlite3")
    await store.initialize()
    workspace = await store.create_workspace("agent-engine")
    conversation = await store.create_conversation(workspace.workspace_id)
    return store, conversation.id


def engine(
    store: ConversationStore,
    runtime: FakeRuntime,
    executor: ToolExecutor,
    sink: FakeEventSink,
    *,
    estimator: FakeEstimator | None = None,
    seed: int = 100,
    max_model_invocations: int = 15,
    max_turn_duration_seconds: float = 900,
) -> AgentEngine:
    return AgentEngine(
        store=store,
        runtime=runtime,
        tool_executor=executor,
        context_builder=ContextBuilder(estimator or FakeEstimator()),
        event_sink=sink,
        system_prompt="Use tools when needed.",
        tool_schemas=(ToolSchema("fake_tool", "A fake tool", {"type": "object"}),),
        model_options={"temperature": 0.3, "presence_penalty": 0},
        seed=seed,
        max_model_invocations=max_model_invocations,
        max_turn_duration_seconds=max_turn_duration_seconds,
    )


def test_direct_response_completes_and_reasoning_is_only_emitted(tmp_path: Path) -> None:
    async def scenario() -> None:
        store, conversation_id = await conversation_store(tmp_path)
        runtime = FakeRuntime([ModelResponse(content="final answer", reasoning="private chain")])
        sink = FakeEventSink()
        agent = engine(store, runtime, FakeToolExecutor(), sink)

        pending = await agent.enqueue(conversation_id, "answer directly")
        finished = await agent.start_next_turn(conversation_id)

        assert finished is not None
        assert finished.request_id == pending.id
        assert finished.terminal_outcome is not None
        assert finished.terminal_outcome.kind is TerminalOutcomeKind.COMPLETED
        assert finished.terminal_outcome.reason_code == "final_response"
        assert runtime.requests[0].seed == 100
        assert finished.base_seed == 100
        assert (await store.list_agent_steps(finished.id))[0].seed == 100
        assert runtime.requests[0].max_output_tokens == 8192
        history = await store.list_canonical_history(conversation_id)
        assert [item.kind for item in history] == [
            CanonicalHistoryEntryKind.USER_MESSAGE,
            CanonicalHistoryEntryKind.MODEL_ATTEMPT,
            CanonicalHistoryEntryKind.FINAL_RESPONSE,
        ]
        assert all("reasoning" not in item.payload for item in history)
        reasoning = [event for event in sink.events if event.kind is AgentEventKind.REASONING]
        assert reasoning[0].payload == {"content": "private chain"}

    asyncio.run(scenario())


def test_turn_timeout_is_limit_reached_and_does_not_start_a_late_effect(
    tmp_path: Path,
) -> None:
    class CancellationSuppressingRuntime(FakeRuntime):
        def __init__(self) -> None:
            super().__init__(())

        async def generate(self, request: ModelRequest) -> ModelResponse:
            self.requests.append(request)
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                return ModelResponse(
                    tool_calls=(ToolCall(id="late", name="fake_tool", arguments={}),)
                )
            raise AssertionError("sleep should have been cancelled by the turn timeout")

    async def scenario() -> None:
        store, conversation_id = await conversation_store(tmp_path)
        runtime = CancellationSuppressingRuntime()
        executor = FakeToolExecutor()

        finished = await engine(
            store,
            runtime,
            executor,
            FakeEventSink(),
            max_turn_duration_seconds=0.01,
        ).run(conversation_id, "time out")

        assert finished.terminal_outcome is not None
        assert finished.terminal_outcome.kind is TerminalOutcomeKind.LIMIT_REACHED
        assert finished.terminal_outcome.reason_code == "turn_time_budget_exhausted"
        assert executor.preflight_batches == []
        assert executor.executed == []

    asyncio.run(scenario())


def test_internal_turn_timeout_is_not_reported_as_cancellation(tmp_path: Path) -> None:
    class SlowRuntime(FakeRuntime):
        def __init__(self) -> None:
            super().__init__(())

        async def generate(self, request: ModelRequest) -> ModelResponse:
            self.requests.append(request)
            await asyncio.sleep(60)
            raise AssertionError("turn timeout should cancel model generation")

    async def scenario() -> None:
        store, conversation_id = await conversation_store(tmp_path)
        finished = await engine(
            store,
            SlowRuntime(),
            FakeToolExecutor(),
            FakeEventSink(),
            max_turn_duration_seconds=0.01,
        ).run(conversation_id, "time out internally")

        assert finished.terminal_outcome is not None
        assert finished.terminal_outcome.kind is TerminalOutcomeKind.LIMIT_REACHED
        assert finished.terminal_outcome.reason_code == "turn_time_budget_exhausted"

    asyncio.run(scenario())


def test_context_budget_exceeded_fails_the_turn(tmp_path: Path) -> None:
    class OversizedEstimator(FakeEstimator):
        def estimate(
            self, messages: Sequence[ModelMessage], tools: Sequence[ToolSchema]
        ) -> int:
            del messages, tools
            return 1_000_000

    async def scenario() -> None:
        store, conversation_id = await conversation_store(tmp_path)
        runtime = FakeRuntime([])

        finished = await engine(
            store,
            runtime,
            FakeToolExecutor(),
            FakeEventSink(),
            estimator=OversizedEstimator(),
        ).run(conversation_id, "too much context")

        assert runtime.requests == []
        assert finished.terminal_outcome is not None
        assert finished.terminal_outcome.kind is TerminalOutcomeKind.FAILED
        assert finished.terminal_outcome.reason_code == "context_budget_exceeded"

    asyncio.run(scenario())


def test_tool_batch_is_preflighted_and_executed_sequentially(tmp_path: Path) -> None:
    async def scenario() -> None:
        store, conversation_id = await conversation_store(tmp_path)
        calls = (
            ToolCall(id="call-1", name="fake_tool", arguments={"value": 1}),
            ToolCall(id="call-2", name="fake_tool", arguments={"value": 2}),
        )
        runtime = FakeRuntime(
            [ModelResponse(tool_calls=calls), ModelResponse(content="used both results")]
        )
        executor = FakeToolExecutor()
        agent = engine(store, runtime, executor, FakeEventSink())

        finished = await agent.run(conversation_id, "use two tools")

        assert finished.terminal_outcome is not None
        assert finished.terminal_outcome.kind is TerminalOutcomeKind.COMPLETED
        assert executor.preflight_batches == [calls]
        assert executor.executed == list(calls)
        assert runtime.requests[1].seed == 101
        assert any(message.tool_call_id == "call-2" for message in runtime.requests[1].messages)
        history = await store.list_canonical_history(conversation_id)
        assert [item.kind for item in history] == [
            CanonicalHistoryEntryKind.USER_MESSAGE,
            CanonicalHistoryEntryKind.MODEL_ATTEMPT,
            CanonicalHistoryEntryKind.TOOL_RESULT,
            CanonicalHistoryEntryKind.TOOL_RESULT,
            CanonicalHistoryEntryKind.MODEL_ATTEMPT,
            CanonicalHistoryEntryKind.FINAL_RESPONSE,
        ]

    asyncio.run(scenario())


def test_fifteenth_invocation_has_no_tools_and_keeps_limit_outcome(tmp_path: Path) -> None:
    async def scenario() -> None:
        store, conversation_id = await conversation_store(tmp_path)
        calls = [
            ToolCall(id=f"call-{index}", name="fake_tool", arguments={"value": index})
            for index in range(1, 15)
        ]
        runtime = FakeRuntime(
            [
                *(ModelResponse(tool_calls=(call,)) for call in calls),
                ModelResponse(content="best effort"),
            ]
        )
        agent = engine(store, runtime, FakeToolExecutor(), FakeEventSink(), seed=0)

        finished = await agent.run(conversation_id, "keep trying")

        assert len(runtime.requests) == 15
        assert runtime.requests[-1].tools == ()
        assert runtime.requests[-1].seed == 14
        assert finished.terminal_outcome is not None
        assert finished.terminal_outcome.kind is TerminalOutcomeKind.LIMIT_REACHED
        assert finished.terminal_outcome.reason_code == "model_invocation_limit"

    asyncio.run(scenario())


def test_two_malformed_model_responses_fail_the_turn(tmp_path: Path) -> None:
    async def scenario() -> None:
        store, conversation_id = await conversation_store(tmp_path)
        runtime = FakeRuntime(
            [
                MalformedModelResponseError("missing message"),
                MalformedModelResponseError("invalid tool call"),
            ]
        )
        executor = FakeToolExecutor()
        finished = await engine(store, runtime, executor, FakeEventSink()).run(
            conversation_id, "malformed twice"
        )

        assert len(runtime.requests) == 2
        assert executor.preflight_batches == []
        assert finished.terminal_outcome is not None
        assert finished.terminal_outcome.kind is TerminalOutcomeKind.FAILED
        assert finished.terminal_outcome.reason_code == "malformed_model_response_limit"
        assert [item.kind for item in await store.list_canonical_history(conversation_id)] == [
            CanonicalHistoryEntryKind.USER_MESSAGE,
            CanonicalHistoryEntryKind.REJECTED_MODEL_ATTEMPT,
            CanonicalHistoryEntryKind.REJECTED_MODEL_ATTEMPT,
        ]

    asyncio.run(scenario())


def test_oversized_tool_batch_is_rejected_once_and_can_be_corrected(tmp_path: Path) -> None:
    async def scenario() -> None:
        store, conversation_id = await conversation_store(tmp_path)
        oversized = tuple(
            ToolCall(id=f"call-{index}", name="fake_tool", arguments={})
            for index in range(5)
        )
        runtime = FakeRuntime(
            [
                ModelResponse(content="attempted batch", tool_calls=oversized),
                ModelResponse(content="corrected"),
            ]
        )
        executor = FakeToolExecutor()

        finished = await engine(store, runtime, executor, FakeEventSink()).run(
            conversation_id, "do not execute oversized batches"
        )

        assert finished.terminal_outcome is not None
        assert finished.terminal_outcome.kind is TerminalOutcomeKind.COMPLETED
        assert executor.preflight_batches == []
        history = await store.list_canonical_history(conversation_id)
        assert [entry.kind for entry in history] == [
            CanonicalHistoryEntryKind.USER_MESSAGE,
            CanonicalHistoryEntryKind.REJECTED_MODEL_ATTEMPT,
            CanonicalHistoryEntryKind.MODEL_ATTEMPT,
            CanonicalHistoryEntryKind.FINAL_RESPONSE,
        ]
        assert history[1].payload["error"] == {
            "code": "tool_calls_per_step_limit",
            "message": "A model response may request at most 4 tool calls.",
        }
        corrective_messages = runtime.requests[1].messages
        assert any(
            "tool_calls_per_step_limit" in message.content
            and message.tool_calls == ()
            for message in corrective_messages
        )

    asyncio.run(scenario())


def test_second_oversized_tool_batch_reaches_rejected_attempt_limit(tmp_path: Path) -> None:
    async def scenario() -> None:
        store, conversation_id = await conversation_store(tmp_path)
        responses = [
            ModelResponse(
                tool_calls=tuple(
                    ToolCall(
                        id=f"batch-{batch}-call-{index}",
                        name="fake_tool",
                        arguments={},
                    )
                    for index in range(5)
                )
            )
            for batch in range(2)
        ]
        executor = FakeToolExecutor()

        finished = await engine(
            store, FakeRuntime(responses), executor, FakeEventSink()
        ).run(conversation_id, "reject twice")

        assert finished.terminal_outcome is not None
        assert finished.terminal_outcome.kind is TerminalOutcomeKind.FAILED
        assert finished.terminal_outcome.reason_code == "rejected_model_attempt_limit"
        assert executor.preflight_batches == []
        history = await store.list_canonical_history(conversation_id)
        assert [entry.kind for entry in history].count(
            CanonicalHistoryEntryKind.REJECTED_MODEL_ATTEMPT
        ) == 2

    asyncio.run(scenario())


def test_first_malformed_response_on_final_invocation_keeps_limit_outcome(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        store, conversation_id = await conversation_store(tmp_path)
        runtime = FakeRuntime(
            [
                ModelResponse(
                    tool_calls=(ToolCall(id="call-1", name="fake_tool", arguments={}),)
                ),
                MalformedModelResponseError("invalid final response"),
            ]
        )
        finished = await engine(
            store,
            runtime,
            FakeToolExecutor(),
            FakeEventSink(),
            max_model_invocations=2,
        ).run(conversation_id, "reach final invocation")

        assert runtime.requests[-1].tools == ()
        assert finished.terminal_outcome is not None
        assert finished.terminal_outcome.kind is TerminalOutcomeKind.LIMIT_REACHED
        assert finished.terminal_outcome.reason_code == "model_invocation_limit"

    asyncio.run(scenario())


def test_blocked_preflight_finishes_without_executing_any_tool(tmp_path: Path) -> None:
    async def scenario() -> None:
        store, conversation_id = await conversation_store(tmp_path)
        calls = (
            ToolCall(id="call-1", name="fake_tool", arguments={}),
            ToolCall(id="call-2", name="fake_tool", arguments={}),
        )
        runtime = FakeRuntime(
            [ModelResponse(tool_calls=calls), ModelResponse(content="cannot write")]
        )
        executor = FakeToolExecutor(
            ToolBatchPreflight(allowed=False, reason_code="write_grant_required")
        )

        finished = await engine(store, runtime, executor, FakeEventSink()).run(
            conversation_id, "blocked"
        )

        assert executor.preflight_batches == [calls]
        assert executor.executed == []
        assert len(runtime.requests) == 2
        assert runtime.requests[-1].tools == ()
        assert any(
            "write_grant_required" in message.content
            for message in runtime.requests[-1].messages
        )
        assert finished.terminal_outcome is not None
        assert finished.terminal_outcome.kind is TerminalOutcomeKind.BLOCKED
        assert finished.terminal_outcome.reason_code == "write_grant_required"
        assert CanonicalHistoryEntryKind.TOOL_RESULT not in {
            item.kind for item in await store.list_canonical_history(conversation_id)
        }
        assert (await store.list_canonical_history(conversation_id))[-1].kind is (
            CanonicalHistoryEntryKind.FINAL_RESPONSE
        )

    asyncio.run(scenario())


def test_registry_preflight_of_full_batch_prevents_earlier_write(tmp_path: Path) -> None:
    async def scenario() -> None:
        store, conversation_id = await conversation_store(tmp_path)
        workspace_root = tmp_path / "workspace"
        workspace_root.mkdir()
        now = datetime.now(UTC)
        executor = RegistryToolExecutor(
            registry=load_config().tool_registry,
            workspace_root=workspace_root,
            session_policy=SessionPolicy(
                conversation_id=conversation_id,
                grants=(
                    Grant(
                        id="workspace-grant",
                        conversation_id=conversation_id,
                        permission="WorkspaceRootGrant",
                        scope="workspace",
                        granted_at=now,
                    ),
                    Grant(
                        id="write-grant",
                        conversation_id=conversation_id,
                        permission="WriteGrant",
                        scope="workspace",
                        granted_at=now,
                    ),
                ),
            ),
        )
        calls = (
            ToolCall(
                id="valid-write",
                name="write_file",
                arguments={"file_path": "must-not-exist.txt", "content": "content"},
            ),
            ToolCall(
                id="invalid-read",
                name="read_file",
                arguments={"file_path": "other.txt", "limit": 0},
            ),
        )
        runtime = FakeRuntime(
            [ModelResponse(tool_calls=calls), ModelResponse(content="invalid batch")]
        )

        finished = await engine(store, runtime, executor, FakeEventSink()).run(
            conversation_id, "invalid batch"
        )

        assert finished.terminal_outcome is not None
        assert finished.terminal_outcome.kind is TerminalOutcomeKind.BLOCKED
        assert finished.terminal_outcome.reason_code == "invalid_tool_arguments"
        assert not (workspace_root / "must-not-exist.txt").exists()

    asyncio.run(scenario())


def test_blocked_tool_result_gets_one_final_invocation_without_tools(tmp_path: Path) -> None:
    class BlockingExecutor(FakeToolExecutor):
        async def execute(self, call: ToolCall) -> ToolResult:
            self.executed.append(call)
            return ToolResult(
                tool_call_id=call.id,
                status=ToolResultStatus.BLOCKED,
                retryable=False,
                data=None,
                error={"code": "effect_blocked", "message": "blocked"},
                meta={"producer": "fake", "truncated": False, "taints": []},
            )

    async def scenario() -> None:
        store, conversation_id = await conversation_store(tmp_path)
        call = ToolCall(id="call-1", name="fake_tool", arguments={})
        runtime = FakeRuntime(
            [ModelResponse(tool_calls=(call,)), ModelResponse(content="blocked final")]
        )

        finished = await engine(
            store, runtime, BlockingExecutor(), FakeEventSink()
        ).run(conversation_id, "blocked result")

        assert finished.terminal_outcome is not None
        assert finished.terminal_outcome.kind is TerminalOutcomeKind.BLOCKED
        assert finished.terminal_outcome.reason_code == "effect_blocked"
        assert len(runtime.requests) == 2
        assert runtime.requests[-1].tools == ()
        history = await store.list_canonical_history(conversation_id)
        assert history[-1].kind is CanonicalHistoryEntryKind.FINAL_RESPONSE
        assert history[-1].payload == {"content": "blocked final"}

    asyncio.run(scenario())


def test_web_taint_blocks_write_before_executor_and_finalizes_without_tools(
    tmp_path: Path,
) -> None:
    class WebTaintExecutor(FakeToolExecutor):
        async def execute(self, call: ToolCall) -> ToolResult:
            self.executed.append(call)
            return ToolResult(
                tool_call_id=call.id,
                status=ToolResultStatus.SUCCESS,
                retryable=False,
                data={"content": "untrusted instructions"},
                error=None,
                meta={
                    "producer": "web_fetch",
                    "truncated": False,
                    "taints": ["UntrustedWebTaint"],
                },
            )

    async def scenario() -> None:
        store, conversation_id = await conversation_store(tmp_path)
        web_call = ToolCall(id="web-1", name="fake_tool", arguments={})
        write_call = ToolCall(
            id="write-1",
            name="write_file",
            arguments={"file_path": "unsafe.txt", "content": "unsafe"},
        )
        runtime = FakeRuntime(
            [
                ModelResponse(tool_calls=(web_call,)),
                ModelResponse(tool_calls=(write_call,)),
                ModelResponse(content="confirmation is required"),
            ]
        )
        executor = WebTaintExecutor()

        finished = await engine(store, runtime, executor, FakeEventSink()).run(
            conversation_id, "research then write"
        )

        assert finished.terminal_outcome is not None
        assert finished.terminal_outcome.kind is TerminalOutcomeKind.BLOCKED
        assert (
            finished.terminal_outcome.reason_code
            == "web_taint_confirmation_required"
        )
        assert executor.preflight_batches == [(web_call,)]
        assert executor.executed == [web_call]
        assert len(runtime.requests) == 3
        assert runtime.requests[-1].tools == ()
        assert "UntrustedWebTaint" in json.dumps(
            [message.content for message in runtime.requests[1].messages]
        )

    asyncio.run(scenario())


def test_unvalidated_token_estimator_blocks_readiness_and_runtime(tmp_path: Path) -> None:
    async def scenario() -> None:
        store, conversation_id = await conversation_store(tmp_path)
        runtime = FakeRuntime([])
        agent = engine(
            store,
            runtime,
            FakeToolExecutor(),
            FakeEventSink(),
            estimator=UnvalidatedEstimator(),
        )

        assert agent.readiness.ready is False
        assert agent.readiness.reason_code == "token_estimator_not_validated"
        finished = await agent.run(conversation_id, "must not run")
        assert runtime.requests == []
        assert finished.terminal_outcome is not None
        assert finished.terminal_outcome.kind is TerminalOutcomeKind.BLOCKED
        assert finished.terminal_outcome.reason_code == "token_estimator_not_validated"

    asyncio.run(scenario())


def test_tool_executor_is_recreated_each_step_to_observe_revocation(tmp_path: Path) -> None:
    class MutableGrant:
        allowed = True

    class PolicyExecutor(FakeToolExecutor):
        def __init__(self, allowed: bool) -> None:
            super().__init__(
                ToolBatchPreflight(
                    allowed=allowed,
                    reason_code=None if allowed else "write_grant_required",
                )
            )

    class PolicyFactory(ToolExecutorFactory):
        def __init__(self, grant: MutableGrant) -> None:
            self.grant = grant
            self.executors: list[PolicyExecutor] = []

        async def create(self, conversation_id: str) -> ToolExecutor:
            del conversation_id
            executor = PolicyExecutor(self.grant.allowed)
            self.executors.append(executor)
            return executor

        async def effective_tool_schemas(
            self, conversation_id: str
        ) -> tuple[ToolSchema, ...]:
            del conversation_id
            return (ToolSchema("fake_tool", "A fake tool", {"type": "object"}),)

    class RevokingRuntime(FakeRuntime):
        def __init__(self, grant: MutableGrant) -> None:
            super().__init__(
                [
                    ModelResponse(
                        tool_calls=(ToolCall(id="call-1", name="fake_tool", arguments={}),)
                    ),
                    ModelResponse(
                        tool_calls=(ToolCall(id="call-2", name="fake_tool", arguments={}),)
                    ),
                ]
            )
            self.grant = grant

        async def generate(self, request: ModelRequest) -> ModelResponse:
            response = await super().generate(request)
            if len(self.requests) == 2:
                self.grant.allowed = False
            return response

    async def scenario() -> None:
        store, conversation_id = await conversation_store(tmp_path)
        grant = MutableGrant()
        factory = PolicyFactory(grant)
        runtime = RevokingRuntime(grant)
        agent = AgentEngine(
            store=store,
            runtime=runtime,
            tool_executor_factory=factory,
            context_builder=ContextBuilder(FakeEstimator()),
            event_sink=FakeEventSink(),
            system_prompt="Use tools when needed.",
            tool_schemas=(ToolSchema("fake_tool", "A fake tool", {"type": "object"}),),
            model_options={},
            seed=0,
        )

        finished = await agent.run(conversation_id, "try two effects")

        assert len(factory.executors) == 2
        assert factory.executors[0].executed[0].id == "call-1"
        assert factory.executors[1].executed == []
        assert finished.terminal_outcome is not None
        assert finished.terminal_outcome.kind is TerminalOutcomeKind.BLOCKED
        assert finished.terminal_outcome.reason_code == "write_grant_required"

    asyncio.run(scenario())


def test_tool_schema_snapshot_observes_grants_activated_and_revoked_between_steps(
    tmp_path: Path,
) -> None:
    class MutablePolicyFactory(ToolExecutorFactory):
        def __init__(self) -> None:
            self.write_allowed = False
            self.snapshots: list[tuple[str, ...]] = []
            self.executor = FakeToolExecutor()

        async def effective_tool_schemas(
            self, conversation_id: str
        ) -> tuple[ToolSchema, ...]:
            del conversation_id
            schemas = [ToolSchema("fake_tool", "read", {"type": "object"})]
            if self.write_allowed:
                schemas.append(ToolSchema("write_file", "write", {"type": "object"}))
            snapshot = tuple(schemas)
            self.snapshots.append(tuple(schema.name for schema in snapshot))
            return snapshot

        async def create(self, conversation_id: str) -> ToolExecutor:
            del conversation_id
            return self.executor

    class GrantChangingRuntime(FakeRuntime):
        def __init__(self, factory: MutablePolicyFactory) -> None:
            super().__init__(
                (
                    ModelResponse(
                        tool_calls=(ToolCall(id="call-1", name="fake_tool", arguments={}),)
                    ),
                    ModelResponse(
                        tool_calls=(ToolCall(id="call-2", name="fake_tool", arguments={}),)
                    ),
                    ModelResponse(content="final"),
                )
            )
            self.factory = factory

        async def generate(self, request: ModelRequest) -> ModelResponse:
            response = await super().generate(request)
            if len(self.requests) == 1:
                self.factory.write_allowed = True
            elif len(self.requests) == 2:
                self.factory.write_allowed = False
            return response

    async def scenario() -> None:
        store, conversation_id = await conversation_store(tmp_path)
        factory = MutablePolicyFactory()
        runtime = GrantChangingRuntime(factory)
        agent = AgentEngine(
            store=store,
            runtime=runtime,
            tool_executor_factory=factory,
            context_builder=ContextBuilder(FakeEstimator()),
            event_sink=FakeEventSink(),
            system_prompt="Use tools when needed.",
            tool_schemas=(),
            model_options={},
            seed=7,
        )

        finished = await agent.run(conversation_id, "observe policy changes")

        assert finished.terminal_outcome is not None
        assert finished.terminal_outcome.kind is TerminalOutcomeKind.COMPLETED
        assert factory.snapshots == [
            ("fake_tool",),
            ("fake_tool", "write_file"),
            ("fake_tool",),
        ]
        assert [tuple(tool.name for tool in request.tools) for request in runtime.requests] == [
            ("fake_tool",),
            ("fake_tool", "write_file"),
            ("fake_tool",),
        ]

    asyncio.run(scenario())
