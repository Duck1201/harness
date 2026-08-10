import asyncio
from collections.abc import Mapping, Sequence
from contextlib import suppress

from .context_builder import (
    ContextBudgetExceeded,
    ContextBuilder,
    ContextTurn,
    ModelContext,
)
from .conversation_store import ConversationStore
from .domain import (
    CanonicalHistoryEntry,
    CanonicalHistoryEntryKind,
    JsonValue,
    PendingRequest,
    TerminalOutcomeKind,
    ToolCall,
    ToolResult,
    ToolResultStatus,
    Turn,
)
from .ports import (
    AgentEvent,
    AgentEventKind,
    EngineReadiness,
    EventSink,
    MalformedModelResponseError,
    ModelRequest,
    ModelResponse,
    ModelRuntime,
    NeverStopSignal,
    StopSignal,
    ToolExecutor,
    ToolExecutorFactory,
    ToolSchema,
)


class AgentEngine:
    def __init__(
        self,
        *,
        store: ConversationStore,
        runtime: ModelRuntime,
        tool_executor: ToolExecutor | None = None,
        tool_executor_factory: ToolExecutorFactory | None = None,
        context_builder: ContextBuilder,
        event_sink: EventSink,
        system_prompt: str,
        tool_schemas: Sequence[ToolSchema],
        model_options: Mapping[str, JsonValue],
        seed: int | None,
        max_model_invocations: int = 15,
        max_tool_calls_per_step: int = 4,
        max_tool_calls_per_turn: int = 20,
        max_turn_duration_seconds: float = 900,
        runtime_readiness: EngineReadiness | None = None,
        stop_signal: StopSignal | None = None,
    ) -> None:
        if max_model_invocations < 1:
            raise ValueError("max_model_invocations must be positive")
        if max_tool_calls_per_step < 1 or max_tool_calls_per_turn < 1:
            raise ValueError("tool call limits must be positive")
        if max_turn_duration_seconds <= 0:
            raise ValueError("max_turn_duration_seconds must be positive")
        if seed is not None and (seed < 0 or seed + max_model_invocations - 1 > 2**63 - 1):
            raise ValueError("seed range must fit in a non-negative signed 64-bit integer")
        if (tool_executor is None) == (tool_executor_factory is None):
            raise ValueError("provide exactly one tool executor or tool executor factory")
        self._store = store
        self._runtime = runtime
        self._tool_executor_factory = (
            tool_executor_factory
            if tool_executor_factory is not None
            else _StaticToolExecutorFactory(tool_executor, tool_schemas)
        )
        self._context_builder = context_builder
        self._event_sink = event_sink
        self._system_prompt = system_prompt
        self._model_options = model_options
        self._seed = seed
        self._max_model_invocations = max_model_invocations
        self._max_tool_calls_per_step = max_tool_calls_per_step
        self._max_tool_calls_per_turn = max_tool_calls_per_turn
        self._max_turn_duration_seconds = max_turn_duration_seconds
        self._runtime_readiness = runtime_readiness or EngineReadiness(ready=True)
        self._stop_signal = stop_signal or NeverStopSignal()

    @property
    def readiness(self) -> EngineReadiness:
        if not self._runtime_readiness.ready:
            return self._runtime_readiness
        return self._context_builder.readiness

    async def enqueue(self, conversation_id: str, content: str) -> PendingRequest:
        return await self._store.enqueue_request(conversation_id, content)

    async def run(self, conversation_id: str, content: str) -> Turn:
        await self.enqueue(conversation_id, content)
        turn = await self.start_next_turn(conversation_id)
        if turn is None:
            raise RuntimeError("queued request did not produce a turn")
        return turn

    async def start_next_turn(self, conversation_id: str) -> Turn | None:
        turn = await self._store.start_next_turn(conversation_id, base_seed=self._seed)
        if turn is None:
            return None
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._max_turn_duration_seconds
        try:
            try:
                async with asyncio.timeout(self._max_turn_duration_seconds):
                    request = await self._store.get_request(turn.request_id)
                    await self._store.append_canonical_history(
                        turn.id,
                        CanonicalHistoryEntryKind.USER_MESSAGE,
                        {"content": request.content},
                    )
                    readiness = self.readiness
                    if not readiness.ready:
                        return await self._finish(
                            turn,
                            TerminalOutcomeKind.BLOCKED,
                            readiness.reason_code or "engine_not_ready",
                        )
                    if self._stop_signal.stop_requested:
                        return await self._finish(
                            turn, TerminalOutcomeKind.CANCELLED, "operator_stop"
                        )
                    return await self._run_active_turn(turn, deadline)
            except TimeoutError:
                if not _deadline_reached(deadline):
                    raise
                return await self._finish(
                    turn,
                    TerminalOutcomeKind.LIMIT_REACHED,
                    "turn_time_budget_exhausted",
                )
        except ContextBudgetExceeded as error:
            return await self._finish(
                turn,
                TerminalOutcomeKind.FAILED,
                "context_budget_exceeded",
                detail=str(error),
            )
        except asyncio.CancelledError:
            await self._finish(turn, TerminalOutcomeKind.CANCELLED, "engine_cancelled")
            raise
        except Exception as error:
            return await self._finish(
                turn,
                TerminalOutcomeKind.FAILED,
                "engine_error",
                detail=f"{type(error).__name__}: {error}",
            )

    async def _run_active_turn(self, turn: Turn, deadline: float) -> Turn:
        rejected_count = 0
        tool_call_count = 0
        for step_sequence in range(1, self._max_model_invocations + 1):
            step_seed = turn.base_seed + step_sequence - 1
            if _deadline_reached(deadline):
                return await self._finish_time_limit(turn)
            if self._stop_signal.stop_requested:
                return await self._finish(turn, TerminalOutcomeKind.CANCELLED, "operator_stop")
            await self._emit(
                AgentEvent(
                    kind=AgentEventKind.STEP_STARTED,
                    turn_id=turn.id,
                    step_sequence=step_sequence,
                    payload={"base_seed": turn.base_seed, "seed": step_seed},
                    conversation_id=turn.conversation_id,
                    request_id=turn.request_id,
                )
            )
            effective_tools = await self._tool_executor_factory.effective_tool_schemas(
                turn.conversation_id
            )
            if _deadline_reached(deadline):
                return await self._finish_time_limit(turn)
            offered_tools = () if step_sequence == self._max_model_invocations else effective_tools
            context = await self._build_context(turn, offered_tools)
            if _deadline_reached(deadline):
                return await self._finish_time_limit(turn)
            request = ModelRequest(
                messages=context.messages,
                tools=tuple(offered_tools),
                options=self._model_options,
                seed=step_seed,
                max_output_tokens=context.output_budget,
                think=True,
            )
            try:
                response = await self._runtime.generate(request)
                _validate_response(response)
            except MalformedModelResponseError as error:
                rejected_count += 1
                await self._store.append_canonical_history(
                    turn.id,
                    CanonicalHistoryEntryKind.REJECTED_MODEL_ATTEMPT,
                    _malformed_payload(error),
                )
                await self._store.append_agent_step(turn.id, seed=step_seed)
                await self._emit_step_finished(turn, step_sequence)
                if rejected_count >= 2:
                    return await self._finish(
                        turn,
                        TerminalOutcomeKind.FAILED,
                        "malformed_model_response_limit",
                    )
                if step_sequence == self._max_model_invocations:
                    return await self._finish(
                        turn,
                        TerminalOutcomeKind.LIMIT_REACHED,
                        "model_invocation_limit",
                    )
                continue

            if self._stop_signal.stop_requested:
                return await self._finish(turn, TerminalOutcomeKind.CANCELLED, "operator_stop")
            if _deadline_reached(deadline):
                await self._emit_step_finished(turn, step_sequence)
                return await self._finish_time_limit(turn)

            await self._emit_reasoning(turn, step_sequence, response)
            if len(response.tool_calls) > self._max_tool_calls_per_step:
                rejected_count += 1
                await self._store.append_canonical_history(
                    turn.id,
                    CanonicalHistoryEntryKind.REJECTED_MODEL_ATTEMPT,
                    _oversized_batch_payload(response, self._max_tool_calls_per_step),
                )
                await self._store.append_agent_step(
                    turn.id, seed=step_seed, tool_calls=response.tool_calls
                )
                await self._emit_step_finished(turn, step_sequence)
                if rejected_count >= 2:
                    return await self._finish(
                        turn,
                        TerminalOutcomeKind.FAILED,
                        "rejected_model_attempt_limit",
                    )
                if step_sequence == self._max_model_invocations:
                    return await self._finish(
                        turn,
                        TerminalOutcomeKind.LIMIT_REACHED,
                        "model_invocation_limit",
                    )
                continue

            await self._store.append_canonical_history(
                turn.id,
                CanonicalHistoryEntryKind.MODEL_ATTEMPT,
                _model_attempt_payload(response),
            )
            for call in response.tool_calls:
                await self._emit(
                    AgentEvent(
                        kind=AgentEventKind.TOOL_CALL,
                        turn_id=turn.id,
                        step_sequence=step_sequence,
                        payload=_tool_call_payload(call),
                        conversation_id=turn.conversation_id,
                        request_id=turn.request_id,
                    )
                )

            if step_sequence == self._max_model_invocations:
                await self._store.append_agent_step(
                    turn.id, seed=step_seed, tool_calls=response.tool_calls
                )
                if response.content is not None:
                    await self._append_final_response(turn, step_sequence, response.content)
                await self._emit_step_finished(turn, step_sequence)
                return await self._finish(
                    turn,
                    TerminalOutcomeKind.LIMIT_REACHED,
                    "model_invocation_limit",
                )

            if not response.tool_calls:
                await self._store.append_agent_step(turn.id, seed=step_seed)
                if response.content is None:
                    raise MalformedModelResponseError("model response has no final content")
                await self._append_final_response(turn, step_sequence, response.content)
                await self._emit_step_finished(turn, step_sequence)
                return await self._finish(
                    turn,
                    TerminalOutcomeKind.COMPLETED,
                    "final_response",
                )

            calls = response.tool_calls
            if tool_call_count + len(calls) > self._max_tool_calls_per_turn:
                await self._store.append_agent_step(turn.id, seed=step_seed, tool_calls=calls)
                await self._emit_step_finished(turn, step_sequence)
                return await self._finish(
                    turn,
                    TerminalOutcomeKind.LIMIT_REACHED,
                    "tool_calls_per_turn_limit",
                )

            if _requires_web_taint_confirmation(calls, context):
                await self._append_blocked_automation(turn, "web_taint_confirmation_required")
                await self._store.append_agent_step(turn.id, seed=step_seed, tool_calls=calls)
                await self._emit_step_finished(turn, step_sequence)
                return await self._finalize_blocked(
                    turn,
                    step_sequence,
                    "web_taint_confirmation_required",
                    deadline=deadline,
                )

            tool_executor = await self._tool_executor_factory.create(turn.conversation_id)
            if self._stop_signal.stop_requested:
                return await self._finish(turn, TerminalOutcomeKind.CANCELLED, "operator_stop")
            if _deadline_reached(deadline):
                return await self._finish_time_limit(turn)
            preflight = await tool_executor.preflight(calls)
            if _deadline_reached(deadline):
                return await self._finish_time_limit(turn)
            if not preflight.allowed:
                await self._append_blocked_automation(
                    turn,
                    preflight.reason_code or "tool_batch_blocked",
                    detail=preflight.detail,
                )
                await self._store.append_agent_step(turn.id, seed=step_seed, tool_calls=calls)
                await self._emit_step_finished(turn, step_sequence)
                return await self._finalize_blocked(
                    turn,
                    step_sequence,
                    preflight.reason_code or "tool_batch_blocked",
                    deadline=deadline,
                    detail=preflight.detail,
                )

            results: list[ToolResult] = []
            for call in calls:
                if self._stop_signal.stop_requested:
                    return await self._finish(turn, TerminalOutcomeKind.CANCELLED, "operator_stop")
                if _deadline_reached(deadline):
                    return await self._finish_time_limit(turn)
                result = await tool_executor.execute(call)
                if result.tool_call_id != call.id:
                    raise ValueError("tool result does not match its call")
                results.append(result)
                await self._store.append_canonical_history(
                    turn.id,
                    CanonicalHistoryEntryKind.TOOL_RESULT,
                    _tool_result_payload(call, result),
                )
                await self._emit(
                    AgentEvent(
                        kind=AgentEventKind.TOOL_RESULT,
                        turn_id=turn.id,
                        step_sequence=step_sequence,
                        payload={
                            "tool_call_id": result.tool_call_id,
                            "status": result.status.value,
                            "retryable": result.retryable,
                            "data": result.data,
                            "error": result.error,
                            "meta": result.meta,
                        },
                        conversation_id=turn.conversation_id,
                        request_id=turn.request_id,
                    )
                )
            await self._store.append_agent_step(
                turn.id, seed=step_seed, tool_calls=calls, tool_results=results
            )
            await self._emit_step_finished(turn, step_sequence)
            tool_call_count += len(calls)
            blocked = next(
                (result for result in results if result.status is ToolResultStatus.BLOCKED),
                None,
            )
            if blocked is not None:
                return await self._finalize_blocked(
                    turn,
                    step_sequence,
                    _tool_error_code(blocked) or "tool_result_blocked",
                    deadline=deadline,
                )

        raise RuntimeError("agent loop exited without a terminal outcome")

    async def _finalize_blocked(
        self,
        turn: Turn,
        blocked_step_sequence: int,
        reason_code: str,
        *,
        deadline: float,
        detail: str | None = None,
    ) -> Turn:
        if (
            blocked_step_sequence >= self._max_model_invocations
            or self._stop_signal.stop_requested
            or _deadline_reached(deadline)
        ):
            return await self._finish(turn, TerminalOutcomeKind.BLOCKED, reason_code, detail=detail)

        step_sequence = blocked_step_sequence + 1
        step_seed = turn.base_seed + step_sequence - 1
        await self._emit(
            AgentEvent(
                kind=AgentEventKind.STEP_STARTED,
                turn_id=turn.id,
                step_sequence=step_sequence,
                payload={"base_seed": turn.base_seed, "seed": step_seed},
                conversation_id=turn.conversation_id,
                request_id=turn.request_id,
            )
        )
        try:
            context = await self._build_context(turn, ())
        except ContextBudgetExceeded:
            await self._emit_step_finished(turn, step_sequence)
            return await self._finish(turn, TerminalOutcomeKind.BLOCKED, reason_code, detail=detail)
        if _deadline_reached(deadline):
            await self._emit_step_finished(turn, step_sequence)
            return await self._finish(turn, TerminalOutcomeKind.BLOCKED, reason_code, detail=detail)

        request = ModelRequest(
            messages=context.messages,
            tools=(),
            options=self._model_options,
            seed=step_seed,
            max_output_tokens=context.output_budget,
            think=True,
        )
        try:
            response = await self._runtime.generate(request)
            _validate_response(response)
        except MalformedModelResponseError as error:
            await self._store.append_canonical_history(
                turn.id,
                CanonicalHistoryEntryKind.REJECTED_MODEL_ATTEMPT,
                _malformed_payload(error),
            )
            await self._store.append_agent_step(turn.id, seed=step_seed)
            await self._emit_step_finished(turn, step_sequence)
            return await self._finish(turn, TerminalOutcomeKind.BLOCKED, reason_code, detail=detail)
        except Exception:
            await self._emit_step_finished(turn, step_sequence)
            return await self._finish(turn, TerminalOutcomeKind.BLOCKED, reason_code, detail=detail)

        if _deadline_reached(deadline):
            await self._emit_step_finished(turn, step_sequence)
            return await self._finish_time_limit(turn)

        await self._emit_reasoning(turn, step_sequence, response)
        await self._store.append_canonical_history(
            turn.id,
            CanonicalHistoryEntryKind.MODEL_ATTEMPT,
            _model_attempt_payload(response),
        )
        await self._store.append_agent_step(turn.id, seed=step_seed, tool_calls=response.tool_calls)
        if response.content is not None:
            await self._append_final_response(turn, step_sequence, response.content)
        await self._emit_step_finished(turn, step_sequence)
        return await self._finish(turn, TerminalOutcomeKind.BLOCKED, reason_code, detail=detail)

    async def _finish_time_limit(self, turn: Turn) -> Turn:
        return await self._finish(
            turn,
            TerminalOutcomeKind.LIMIT_REACHED,
            "turn_time_budget_exhausted",
        )

    async def _append_blocked_automation(
        self,
        turn: Turn,
        reason_code: str,
        *,
        detail: str | None = None,
    ) -> None:
        await self._store.append_canonical_history(
            turn.id,
            CanonicalHistoryEntryKind.INTERNAL_AUTOMATION,
            {
                "automation_id": "tool_batch_blocked",
                "status": "blocked",
                "reason_code": reason_code,
                "detail": detail,
            },
        )

    async def _build_context(self, turn: Turn, offered_tools: Sequence[ToolSchema]) -> ModelContext:
        history = await self._store.list_canonical_history(turn.conversation_id)
        grouped: dict[str, list[CanonicalHistoryEntry]] = {}
        for entry in history:
            grouped.setdefault(entry.turn_id, []).append(entry)
        current = ContextTurn(turn_id=turn.id, entries=tuple(grouped.pop(turn.id, ())))
        completed = tuple(
            ContextTurn(turn_id=turn_id, entries=tuple(entries))
            for turn_id, entries in grouped.items()
        )
        return self._context_builder.build(
            system=self._system_prompt,
            tool_schemas=offered_tools,
            completed_turns=completed,
            current_turn=current,
        )

    async def _append_final_response(self, turn: Turn, step_sequence: int, content: str) -> None:
        await self._store.append_canonical_history(
            turn.id,
            CanonicalHistoryEntryKind.FINAL_RESPONSE,
            {"content": content},
        )
        await self._emit(
            AgentEvent(
                kind=AgentEventKind.FINAL_RESPONSE,
                turn_id=turn.id,
                step_sequence=step_sequence,
                payload={"content": content},
                conversation_id=turn.conversation_id,
                request_id=turn.request_id,
            )
        )

    async def _emit_reasoning(
        self, turn: Turn, step_sequence: int, response: ModelResponse
    ) -> None:
        if response.reasoning is None:
            return
        await self._emit(
            AgentEvent(
                kind=AgentEventKind.REASONING,
                turn_id=turn.id,
                step_sequence=step_sequence,
                payload={"content": response.reasoning},
                conversation_id=turn.conversation_id,
                request_id=turn.request_id,
            )
        )

    async def _emit_step_finished(self, turn: Turn, step_sequence: int) -> None:
        await self._emit(
            AgentEvent(
                kind=AgentEventKind.STEP_FINISHED,
                turn_id=turn.id,
                step_sequence=step_sequence,
                payload={},
                conversation_id=turn.conversation_id,
                request_id=turn.request_id,
            )
        )

    async def _finish(
        self,
        turn: Turn,
        kind: TerminalOutcomeKind,
        reason_code: str,
        *,
        detail: str | None = None,
    ) -> Turn:
        finished = await self._store.finish_turn(
            turn.id,
            kind,
            reason_code=reason_code,
            detail=detail,
        )
        await self._emit(
            AgentEvent(
                kind=AgentEventKind.TURN_FINISHED,
                turn_id=turn.id,
                step_sequence=None,
                payload={"outcome_kind": kind.value, "reason_code": reason_code},
                conversation_id=turn.conversation_id,
                request_id=turn.request_id,
            )
        )
        return finished

    async def _emit(self, event: AgentEvent) -> None:
        with suppress(Exception):
            await self._event_sink.emit(event)


def _validate_response(response: ModelResponse) -> None:
    if response.content is None and not response.tool_calls:
        raise MalformedModelResponseError("model response has neither content nor tool calls")
    for call in response.tool_calls:
        if not call.id or not call.name:
            raise MalformedModelResponseError("model response contains an invalid tool call")


def _model_attempt_payload(response: ModelResponse) -> Mapping[str, JsonValue]:
    return {
        "content": response.content,
        "tool_calls": [_tool_call_payload(call) for call in response.tool_calls],
    }


def _tool_call_payload(call: ToolCall) -> Mapping[str, JsonValue]:
    return {
        "id": call.id,
        "name": call.name,
        "arguments": call.arguments,
        "idempotency_key": call.idempotency_key,
    }


def _tool_result_payload(call: ToolCall, result: ToolResult) -> Mapping[str, JsonValue]:
    return {
        "tool_call_id": result.tool_call_id,
        "tool_name": call.name,
        "status": result.status.value,
        "retryable": result.retryable,
        "data": result.data,
        "error": result.error,
        "meta": result.meta,
    }


def _malformed_payload(error: MalformedModelResponseError) -> Mapping[str, JsonValue]:
    payload: dict[str, JsonValue] = {
        "content": None,
        "error": {"code": "malformed_model_response", "message": str(error)},
    }
    if error.raw is not None:
        payload["raw"] = _without_reasoning(error.raw)
    return payload


def _oversized_batch_payload(response: ModelResponse, limit: int) -> Mapping[str, JsonValue]:
    return {
        **_model_attempt_payload(response),
        "error": {
            "code": "tool_calls_per_step_limit",
            "message": f"A model response may request at most {limit} tool calls.",
        },
    }


def _without_reasoning(value: JsonValue) -> JsonValue:
    if isinstance(value, Mapping):
        return {
            key: _without_reasoning(child)
            for key, child in value.items()
            if not _is_reasoning_key(key)
        }
    if isinstance(value, Sequence) and not isinstance(value, str):
        return [_without_reasoning(child) for child in value]
    return value


def _is_reasoning_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    return normalized in {"reasoning", "thinking"} or normalized.endswith(
        ("_reasoning", "_thinking")
    )


def _tool_error_code(result: ToolResult) -> str | None:
    if result.error is None:
        return None
    code = result.error.get("code")
    return code if isinstance(code, str) else None


def _requires_web_taint_confirmation(calls: Sequence[ToolCall], context: ModelContext) -> bool:
    return "UntrustedWebTaint" in context.taints and any(
        call.name in {"write_file", "edit"} for call in calls
    )


def _deadline_reached(deadline: float) -> bool:
    return asyncio.get_running_loop().time() >= deadline


class _StaticToolExecutorFactory:
    def __init__(self, executor: ToolExecutor | None, tool_schemas: Sequence[ToolSchema]) -> None:
        if executor is None:
            raise ValueError("tool executor is required")
        self._executor = executor
        self._tool_schemas = tuple(tool_schemas)

    async def effective_tool_schemas(self, conversation_id: str) -> tuple[ToolSchema, ...]:
        del conversation_id
        return self._tool_schemas

    async def create(self, conversation_id: str) -> ToolExecutor:
        del conversation_id
        return self._executor
