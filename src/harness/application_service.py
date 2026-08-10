import asyncio
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .agent_engine import AgentEngine
from .composite_tools import CompositeToolExecutor
from .config import HarnessConfig, ToolRegistryConfig
from .context_builder import ContextBuilder
from .conversation_store import ConversationStore
from .domain import (
    Conversation,
    Feedback,
    Grant,
    JsonValue,
    PendingRequest,
    ToolCall,
    ToolResult,
    TurnStatus,
)
from .evals import (
    BenchmarkLease,
    ContractCaseRunner,
    EvalService,
    EvalStore,
    EvalTier,
    RegressionDraft,
    load_eval_catalog,
)
from .local_tools import RegistryToolExecutor
from .observability_store import ObservabilityStore
from .ports import (
    AgentEvent,
    EngineReadiness,
    ModelRuntime,
    StopSignal,
    TokenEstimator,
    ToolBatchPreflight,
    ToolExecutor,
    ToolExecutorFactory,
    ToolSchema,
)
from .web_tools import WebToolExecutor
from .workspace_coordinator import WorkspaceCoordinator


class ApplicationRuntime(ModelRuntime, Protocol):
    async def verify_profile(self) -> "RuntimeVerification": ...

    async def aclose(self) -> None: ...


class RuntimeVerification(Protocol):
    @property
    def ready(self) -> bool: ...

    @property
    def reason_code(self) -> str | None: ...


@dataclass(frozen=True, slots=True)
class WorkspaceInfo:
    id: str
    root: Path


class ApplicationServiceError(Exception):
    def __init__(self, code: str, message: str, *, status_code: int) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


class ApplicationService:
    def __init__(
        self,
        *,
        store: ConversationStore,
        observability_store: ObservabilityStore,
        config: HarnessConfig,
        runtime: ApplicationRuntime,
        estimator: TokenEstimator,
        allowed_workspace_roots: Sequence[str | Path],
        brave_api_key: str | None = None,
        benchmark_lease: BenchmarkLease | None = None,
        eval_service: EvalService | None = None,
    ) -> None:
        roots: list[Path] = []
        for candidate in allowed_workspace_roots:
            path = Path(candidate)
            if not path.is_absolute():
                raise ValueError("allowed workspace roots must be absolute")
            resolved = path.resolve(strict=True)
            if not resolved.is_dir():
                raise ValueError("allowed workspace roots must be directories")
            if resolved not in roots:
                roots.append(resolved)
        self.store = store
        self.observability_store = observability_store
        self.config = config
        self.runtime = runtime
        self.estimator = estimator
        if brave_api_key is not None and not brave_api_key.strip():
            raise ValueError("brave_api_key must not be blank")
        normalized_brave_key = brave_api_key.strip() if brave_api_key is not None else None
        self._allowed_roots = tuple(roots)
        if (
            benchmark_lease is not None
            and eval_service is not None
            and benchmark_lease is not eval_service.lease
        ):
            raise ValueError("eval_service and ApplicationService must share BenchmarkLease")
        self.benchmark_lease = benchmark_lease or (
            eval_service.lease if eval_service is not None else BenchmarkLease()
        )
        self.eval_service = eval_service or _default_eval_service(
            store=store,
            lease=self.benchmark_lease,
            registry=config.tool_registry,
        )
        if Path(self.eval_service.store.database).resolve() == Path(store.database).resolve():
            raise ValueError("EvalStore must be separate from ConversationStore")
        self._workspaces_by_root: dict[Path, WorkspaceInfo] = {}
        self._runtime_readiness = EngineReadiness(ready=False, reason_code="runtime_not_verified")
        self._initialized = False
        self._shutting_down = False
        self._workers: dict[str, asyncio.Task[None]] = {}
        self._stop_signals: dict[str, _CooperativeStopSignal] = {}
        self._worker_lock = asyncio.Lock()
        self._workspace_coordinator = WorkspaceCoordinator(store)
        self._tool_executor_factory: ToolExecutorFactory = _ConversationToolExecutorFactory(
            store=store,
            registry=config.tool_registry,
            workspace_root=self.workspace_root,
            coordinator=self._workspace_coordinator,
            brave_api_key=normalized_brave_key,
        )
        self._event_bus = _LiveEventBus()
        self._event_sink = _ServiceEventSink(self._event_bus, observability_store)

    async def initialize(self) -> None:
        if self._initialized:
            return
        await self.store.initialize()
        await self.observability_store.initialize()
        await self.eval_service.initialize()
        await self.store.recover_stale_active_turns()
        for root in self._allowed_roots:
            revision = await self.store.create_workspace(str(root))
            self._workspaces_by_root[root] = WorkspaceInfo(
                id=revision.workspace_id,
                root=root,
            )
        try:
            verification = await self.runtime.verify_profile()
            self._runtime_readiness = EngineReadiness(
                ready=verification.ready,
                reason_code=verification.reason_code,
            )
        except Exception:
            self._runtime_readiness = EngineReadiness(
                ready=False, reason_code="runtime_verification_failed"
            )
        self._initialized = True
        for conversation in await self.store.list_conversations(include_archived=True):
            if await self.store.list_pending_requests(conversation.id):
                await self._ensure_worker(conversation.id)

    async def shutdown(self) -> None:
        self._shutting_down = True
        tasks = tuple(self._workers.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await self.eval_service.shutdown()
        await self.runtime.aclose()

    @property
    def readiness(self) -> EngineReadiness:
        if not self._runtime_readiness.ready:
            return self._runtime_readiness
        if not self.estimator.validated:
            readiness = getattr(self.estimator, "readiness", None)
            if isinstance(readiness, EngineReadiness):
                return readiness
            return EngineReadiness(ready=False, reason_code="token_estimator_not_validated")
        return EngineReadiness(ready=True)

    def list_workspaces(self) -> tuple[WorkspaceInfo, ...]:
        return tuple(self._workspaces_by_root.values())

    async def create_conversation(
        self, workspace_root: str, *, name: str = "New conversation"
    ) -> Conversation:
        candidate = Path(workspace_root)
        if not candidate.is_absolute():
            raise ApplicationServiceError(
                "workspace_root_must_be_absolute",
                "Workspace roots supplied to the API must be absolute.",
                status_code=422,
            )
        try:
            root = candidate.resolve(strict=True)
        except OSError as error:
            raise ApplicationServiceError(
                "workspace_root_not_allowed",
                "The workspace root is not in the server allowlist.",
                status_code=403,
            ) from error
        workspace = self._workspaces_by_root.get(root)
        if workspace is None:
            raise ApplicationServiceError(
                "workspace_root_not_allowed",
                "The workspace root is not in the server allowlist.",
                status_code=403,
            )
        conversation = await self.store.create_conversation(workspace.id, name=name)
        await self.store.grant(
            conversation.id,
            permission="WorkspaceRootGrant",
            scope=str(root),
        )
        return conversation

    async def get_conversation(self, conversation_id: str) -> Conversation:
        return await self.store.get_conversation(conversation_id)

    async def list_conversations(self, *, include_archived: bool = False) -> list[Conversation]:
        return await self.store.list_conversations(include_archived=include_archived)

    async def update_conversation(
        self,
        conversation_id: str,
        *,
        name: str | None = None,
        archived: bool | None = None,
    ) -> Conversation:
        conversation = await self.store.get_conversation(conversation_id)
        if name is not None:
            conversation = await self.store.rename_conversation(conversation_id, name)
        if archived is not None:
            conversation = await self.store.set_conversation_archived(
                conversation_id, archived=archived
            )
        return conversation

    async def delete_conversation(self, conversation_id: str) -> None:
        await self.store.delete_conversation(conversation_id)

    async def enqueue_request(self, conversation_id: str, content: str) -> PendingRequest:
        conversation = await self.store.get_conversation(conversation_id)
        if conversation.archived_at is not None:
            raise ApplicationServiceError(
                "conversation_archived",
                "Archived conversations cannot accept requests.",
                status_code=409,
            )
        pending = await self.store.enqueue_request(conversation_id, content)
        await self._ensure_worker(conversation_id)
        return pending

    async def edit_pending_request(
        self, conversation_id: str, request_id: str, content: str
    ) -> PendingRequest:
        request = await self.store.get_request(request_id)
        if request.conversation_id != conversation_id:
            raise ApplicationServiceError(
                "request_not_in_conversation",
                "The request does not belong to this conversation.",
                status_code=404,
            )
        return await self.store.edit_pending_request(request_id, content)

    async def cancel_pending_request(self, conversation_id: str, request_id: str) -> PendingRequest:
        request = await self.store.get_request(request_id)
        if request.conversation_id != conversation_id:
            raise ApplicationServiceError(
                "request_not_in_conversation",
                "The request does not belong to this conversation.",
                status_code=404,
            )
        return await self.store.cancel_pending_request(request_id)

    async def grant(self, conversation_id: str, permission: str) -> Grant:
        await self.store.get_conversation(conversation_id)
        scopes = {
            "WriteGrant": "workspace",
            "WebAccessGrant": "public-network",
        }
        scope = scopes.get(permission)
        if scope is None:
            raise ApplicationServiceError(
                "grant_type_not_public",
                "Only WriteGrant and WebAccessGrant can be changed through this API.",
                status_code=422,
            )
        return await self.store.grant(conversation_id, permission, scope)

    async def list_grants(self, conversation_id: str) -> tuple[Grant, ...]:
        return (await self.store.get_session_policy(conversation_id)).grants

    async def revoke_grant(self, conversation_id: str, grant_id: str) -> Grant:
        grant = next(
            (
                item
                for item in (await self.store.get_session_policy(conversation_id)).grants
                if item.id == grant_id
            ),
            None,
        )
        if grant is None:
            raise ApplicationServiceError(
                "grant_not_found",
                "The active grant was not found.",
                status_code=404,
            )
        if grant.permission == "WorkspaceRootGrant":
            raise ApplicationServiceError(
                "workspace_root_grant_not_public",
                "WorkspaceRootGrant is managed by the server allowlist.",
                status_code=422,
            )
        return await self.store.revoke_grant(conversation_id, grant_id)

    async def add_feedback(
        self,
        conversation_id: str,
        *,
        rating: int,
        comment: str | None,
        turn_id: str | None,
    ) -> Feedback:
        return await self.store.add_feedback(
            conversation_id,
            rating=rating,
            comment=comment,
            turn_id=turn_id,
        )

    async def create_regression_draft(
        self, conversation_id: str, feedback_id: str
    ) -> RegressionDraft:
        feedback = next(
            (
                item
                for item in await self.store.list_feedback(conversation_id)
                if item.id == feedback_id
            ),
            None,
        )
        if feedback is None:
            raise ApplicationServiceError(
                "feedback_not_found",
                "The feedback was not found in this Conversation.",
                status_code=404,
            )
        if feedback.turn_id is None:
            raise ApplicationServiceError(
                "feedback_not_linked_to_turn",
                "Regression drafts require feedback linked to a Turn.",
                status_code=422,
            )
        turn = next(
            (
                item
                for item in await self.store.list_turns(conversation_id)
                if item.id == feedback.turn_id
            ),
            None,
        )
        if turn is None:
            raise ApplicationServiceError(
                "turn_not_found",
                "The feedback Turn was not found in this Conversation.",
                status_code=404,
            )
        return await self.eval_service.create_regression_draft(feedback, turn)

    async def chat_snapshot(self, conversation_id: str) -> dict[str, object]:
        conversation = await self.store.get_conversation(conversation_id)
        pending = await self.store.list_pending_requests(conversation_id)
        history = await self.store.list_canonical_history(conversation_id)
        turns = await self.store.list_turns(conversation_id)
        feedback = await self.store.list_feedback(conversation_id)
        active = next((turn for turn in turns if turn.status is TurnStatus.ACTIVE), None)
        return {
            "conversation": conversation,
            "pending_requests": pending,
            "history": history,
            "active_turn": active,
            "turns": turns,
            "feedback": feedback,
        }

    async def stop(self, conversation_id: str) -> None:
        await self.store.get_conversation(conversation_id)
        signal = self._stop_signals.get(conversation_id)
        if signal is None:
            raise ApplicationServiceError(
                "no_active_turn",
                "The conversation has no active Turn to stop.",
                status_code=409,
            )
        signal.request_stop()

    def subscribe(self, conversation_id: str) -> asyncio.Queue[AgentEvent]:
        return self._event_bus.subscribe(conversation_id)

    def unsubscribe(self, conversation_id: str, queue: asyncio.Queue[AgentEvent]) -> None:
        self._event_bus.unsubscribe(conversation_id, queue)

    async def _ensure_worker(self, conversation_id: str) -> None:
        async with self._worker_lock:
            if self._shutting_down:
                raise RuntimeError("application service is shutting down")
            worker = self._workers.get(conversation_id)
            if worker is None or worker.done():
                self._workers[conversation_id] = asyncio.create_task(
                    self._run_conversation(conversation_id),
                    name=f"harness-conversation-{conversation_id}",
                )

    async def _run_conversation(self, conversation_id: str) -> None:
        try:
            while True:
                await self.benchmark_lease.enter_chat(conversation_id)
                try:
                    stop_signal = _CooperativeStopSignal()
                    self._stop_signals[conversation_id] = stop_signal
                    engine = self._build_engine(stop_signal)
                    turn = await engine.start_next_turn(conversation_id)
                    if self._stop_signals.get(conversation_id) is stop_signal:
                        self._stop_signals.pop(conversation_id, None)
                finally:
                    await self.benchmark_lease.leave_chat(conversation_id)
                if turn is None:
                    break
        finally:
            self._stop_signals.pop(conversation_id, None)
            async with self._worker_lock:
                current = asyncio.current_task()
                if self._workers.get(conversation_id) is current:
                    self._workers.pop(conversation_id, None)
                    if not self._shutting_down and await self.store.list_pending_requests(
                        conversation_id
                    ):
                        self._workers[conversation_id] = asyncio.create_task(
                            self._run_conversation(conversation_id),
                            name=f"harness-conversation-{conversation_id}",
                        )

    def _build_engine(self, stop_signal: StopSignal) -> AgentEngine:
        return AgentEngine(
            store=self.store,
            runtime=self.runtime,
            tool_executor_factory=self._tool_executor_factory,
            context_builder=ContextBuilder(
                self.estimator,
                context_window=24576,
                output_budget=self.config.loop.max_output_tokens,
            ),
            event_sink=self._event_sink,
            system_prompt=(
                "Use the available tools when needed. All workspace paths supplied to tools "
                "must be relative to the workspace root."
            ),
            tool_schemas=(),
            model_options={
                "temperature": self.config.execution_route.sampling.temperature,
                "presence_penalty": self.config.execution_route.sampling.presence_penalty,
            },
            seed=None,
            max_model_invocations=self.config.loop.max_steps,
            max_tool_calls_per_step=self.config.loop.max_tool_calls_per_step,
            max_tool_calls_per_turn=self.config.loop.max_tool_calls_per_turn,
            max_turn_duration_seconds=self.config.loop.max_turn_duration_seconds,
            runtime_readiness=self._runtime_readiness,
            stop_signal=stop_signal,
        )

    def workspace_root(self, workspace_id: str) -> Path:
        for workspace in self._workspaces_by_root.values():
            if workspace.id == workspace_id:
                return workspace.root
        raise ApplicationServiceError(
            "workspace_not_registered",
            "The workspace is not registered by this server.",
            status_code=404,
        )


def _default_eval_service(
    *,
    store: ConversationStore,
    lease: BenchmarkLease,
    registry: ToolRegistryConfig,
) -> EvalService:
    project_root = Path(__file__).resolve().parents[2]
    catalog = load_eval_catalog(
        project_root / "evals/fixtures/regressions.json",
        project_root / "evals/experiments.json",
        contract_root=project_root,
    )
    conversation_database = Path(store.database)
    if conversation_database.name == ":memory:":
        raise ValueError("ConversationStore must use a file when evals are enabled")
    eval_database = conversation_database.with_name("evals.sqlite3")
    return EvalService(
        store=EvalStore(eval_database),
        catalog=catalog,
        lease=lease,
        runners={EvalTier.CONTRACT: ContractCaseRunner(registry=registry)},
    )


class _ConversationToolExecutorFactory:
    def __init__(
        self,
        *,
        store: ConversationStore,
        registry: ToolRegistryConfig,
        workspace_root: Callable[[str], Path],
        coordinator: WorkspaceCoordinator,
        brave_api_key: str | None,
    ) -> None:
        self._store = store
        self._registry = registry
        self._workspace_root = workspace_root
        self._coordinator = coordinator
        self._brave_api_key = brave_api_key

    async def effective_tool_schemas(self, conversation_id: str) -> tuple[ToolSchema, ...]:
        policy = await self._store.get_session_policy(conversation_id)
        effective_grants = policy.effective_grants
        return tuple(
            definition.tool_schema()
            for definition in self._registry.model_tools
            if definition.status == "enabled"
            and all(grant in effective_grants for grant in definition.required_grants)
            and (definition.name != "web_search" or self._brave_api_key is not None)
        )

    async def create(self, conversation_id: str) -> ToolExecutor:
        conversation = await self._store.get_conversation(conversation_id)
        policy = await self._store.get_session_policy(conversation_id)
        root = self._workspace_root(conversation.workspace_id)
        registry = self._registry
        local_executor = RegistryToolExecutor(
            registry=registry,
            workspace_root=root,
            session_policy=policy,
        )
        local: ToolExecutor = _CoordinatedToolExecutor(
            executor=local_executor,
            coordinator=self._coordinator,
            workspace_id=conversation.workspace_id,
        )
        web = WebToolExecutor(
            registry=registry,
            session_policy=policy,
            brave_api_key=self._brave_api_key,
        )
        routes: dict[str, ToolExecutor] = {}
        for definition in registry.model_tools:
            routes[definition.name] = web if definition.name.startswith("web_") else local
        return CompositeToolExecutor(routes=routes)


class _CoordinatedToolExecutor:
    def __init__(
        self,
        *,
        executor: ToolExecutor,
        coordinator: WorkspaceCoordinator,
        workspace_id: str,
    ) -> None:
        self._executor = executor
        self._coordinator = coordinator
        self._workspace_id = workspace_id

    async def preflight(self, calls: Sequence[ToolCall]) -> ToolBatchPreflight:
        return await self._executor.preflight(calls)

    async def execute(self, call: ToolCall) -> ToolResult:
        return await self._coordinator.execute(self._workspace_id, call, self._executor)


class _CooperativeStopSignal:
    def __init__(self) -> None:
        self._requested = False

    @property
    def stop_requested(self) -> bool:
        return self._requested

    def request_stop(self) -> None:
        self._requested = True


class _LiveEventBus:
    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue[AgentEvent]]] = {}

    def subscribe(self, conversation_id: str) -> asyncio.Queue[AgentEvent]:
        queue: asyncio.Queue[AgentEvent] = asyncio.Queue()
        self._subscribers.setdefault(conversation_id, set()).add(queue)
        return queue

    def unsubscribe(self, conversation_id: str, queue: asyncio.Queue[AgentEvent]) -> None:
        subscribers = self._subscribers.get(conversation_id)
        if subscribers is None:
            return
        subscribers.discard(queue)
        if not subscribers:
            self._subscribers.pop(conversation_id, None)

    async def emit(self, event: AgentEvent) -> None:
        for queue in tuple(self._subscribers.get(event.conversation_id, ())):
            queue.put_nowait(event)


class _ServiceEventSink:
    def __init__(self, bus: _LiveEventBus, observability_store: ObservabilityStore) -> None:
        self._bus = bus
        self._observability_store = observability_store

    async def emit(self, event: AgentEvent) -> None:
        await self._bus.emit(event)
        payload: dict[str, JsonValue] = {
            "kind": event.kind.value,
            "conversation_id": event.conversation_id,
            "request_id": event.request_id,
        }
        for key in (
            "base_seed",
            "id",
            "name",
            "seed",
            "tool_call_id",
            "status",
            "retryable",
            "outcome_kind",
            "reason_code",
        ):
            value = event.payload.get(key)
            if value is not None:
                payload[key] = value
        await self._observability_store.record(
            event_type=f"agent.{event.kind.value}",
            payload=payload,
            turn_id=event.turn_id,
            step_sequence=event.step_sequence,
        )
