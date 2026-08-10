from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from .domain import JsonValue, ToolCall, ToolResult


class ModelRole(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


@dataclass(frozen=True, slots=True)
class ToolSchema:
    name: str
    description: str
    parameters: Mapping[str, JsonValue]


@dataclass(frozen=True, slots=True)
class ModelMessage:
    role: ModelRole
    content: str
    tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: str | None = None
    name: str | None = None


@dataclass(frozen=True, slots=True)
class ModelUsage:
    input_tokens: int
    output_tokens: int
    reasoning_tokens: int | None = None


@dataclass(frozen=True, slots=True)
class ModelDurations:
    total_ns: int | None = None
    load_ns: int | None = None
    prompt_eval_ns: int | None = None
    eval_ns: int | None = None


@dataclass(frozen=True, slots=True)
class ModelRequest:
    messages: tuple[ModelMessage, ...]
    tools: tuple[ToolSchema, ...]
    options: Mapping[str, JsonValue]
    seed: int
    max_output_tokens: int = 8192
    think: bool = True


@dataclass(frozen=True, slots=True)
class ModelResponse:
    content: str | None = None
    reasoning: str | None = None
    tool_calls: tuple[ToolCall, ...] = ()
    usage: ModelUsage | None = None
    durations: ModelDurations | None = None


@dataclass(frozen=True, slots=True)
class ToolBatchPreflight:
    allowed: bool
    reason_code: str | None = None
    detail: str | None = None

    def __post_init__(self) -> None:
        if not self.allowed and not self.reason_code:
            raise ValueError("blocked preflight requires a reason_code")


class AgentEventKind(StrEnum):
    REASONING = "reasoning"
    STEP_STARTED = "step_started"
    STEP_FINISHED = "step_finished"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    CONFIRMATION_REQUIRED = "confirmation_required"
    CONFIRMATION_RESOLVED = "confirmation_resolved"
    FINAL_RESPONSE = "final_response"
    TURN_FINISHED = "turn_finished"


@dataclass(frozen=True, slots=True)
class AgentEvent:
    kind: AgentEventKind
    turn_id: str
    step_sequence: int | None
    payload: Mapping[str, JsonValue]
    conversation_id: str
    request_id: str


@dataclass(frozen=True, slots=True)
class EngineReadiness:
    ready: bool
    reason_code: str | None = None


class ModelRuntime(Protocol):
    async def generate(self, request: ModelRequest) -> ModelResponse: ...


class ModelRuntimeError(Exception):
    pass


class MalformedModelResponseError(ModelRuntimeError):
    def __init__(
        self,
        message: str,
        *,
        raw: Mapping[str, JsonValue] | None = None,
    ) -> None:
        super().__init__(message)
        self.raw = raw


class ToolExecutor(Protocol):
    async def preflight(self, calls: Sequence[ToolCall]) -> ToolBatchPreflight: ...

    async def execute(self, call: ToolCall) -> ToolResult: ...


class ToolExecutorFactory(Protocol):
    async def effective_tool_schemas(self, conversation_id: str) -> tuple[ToolSchema, ...]: ...

    async def create(self, conversation_id: str) -> ToolExecutor: ...


class StopSignal(Protocol):
    @property
    def stop_requested(self) -> bool: ...


class NeverStopSignal:
    @property
    def stop_requested(self) -> bool:
        return False


@dataclass(frozen=True, slots=True)
class ConfirmationRequest:
    id: str
    conversation_id: str
    turn_id: str
    request_id: str
    step_sequence: int
    reason_code: str
    tool_calls: tuple[ToolCall, ...]


@dataclass(frozen=True, slots=True)
class ConfirmationDecision:
    approved: bool
    reason_code: str

    def __post_init__(self) -> None:
        if not self.reason_code:
            raise ValueError("confirmation decision requires a reason_code")


class ConfirmationGate(Protocol):
    """Asks the Operator to confirm a batch the harness refuses to run unattended.

    The gate is responsible for notifying the Operator, because registering the
    request and announcing it must happen together: a decision that arrives before
    the request is known would be dropped.
    """

    async def confirm(self, request: ConfirmationRequest) -> ConfirmationDecision: ...


class DenyingConfirmationGate:
    """Default gate: nothing can approve, so the batch stays blocked.

    Used wherever no Operator is present — eval runners and tests included. The
    reason code is the request's own, because from the Turn's point of view the
    confirmation was required and never obtained.
    """

    async def confirm(self, request: ConfirmationRequest) -> ConfirmationDecision:
        return ConfirmationDecision(approved=False, reason_code=request.reason_code)


class TokenEstimator(Protocol):
    @property
    def validated(self) -> bool: ...

    def estimate(self, messages: Sequence[ModelMessage], tools: Sequence[ToolSchema]) -> int: ...


class EventSink(Protocol):
    async def emit(self, event: AgentEvent) -> None: ...


class NullEventSink:
    async def emit(self, event: AgentEvent) -> None:
        del event
