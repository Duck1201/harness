import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol, cast

from ..agent_engine import AgentEngine
from ..config import ToolRegistryConfig
from ..context_builder import ContextBuilder, ContextTurn
from ..conversation_store import ConversationStore
from ..domain import (
    CanonicalHistoryEntry,
    CanonicalHistoryEntryKind,
    Grant,
    JsonValue,
    SessionPolicy,
    TerminalOutcomeKind,
    ToolCall,
    ToolResult,
    ToolResultStatus,
)
from ..local_tools import RegistryToolExecutor
from ..ports import (
    ConfirmationPreview,
    EngineReadiness,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    NullEventSink,
    ToolBatchPreflight,
    ToolSchema,
)
from ..web_tools import WebToolExecutor
from .models import EvalTier, RegressionFixture, TaskVerdict
from .oracles import EvalEvidence, OracleEvaluation, evaluate_oracle


@dataclass(frozen=True, slots=True)
class EvalCaseSpec:
    run_id: str
    arm_id: str
    fixture: RegressionFixture
    seed: int
    order_index: int
    tier: EvalTier
    # O que o braço muda em relação ao contrato. Vinha sendo gravado no EvalStore
    # e parava ali: dois braços declarados diferentes rodavam idênticos, e a
    # comparação media ruído. Quem aplica é o runner, campo por campo.
    settings: Mapping[str, JsonValue] = field(
        default_factory=lambda: cast(Mapping[str, JsonValue], {})
    )


@dataclass(frozen=True, slots=True)
class CaseRunResult:
    evidence: EvalEvidence
    metrics: Mapping[str, float]
    security_violations: int = 0
    evaluation: OracleEvaluation | None = None


class CaseRunner(Protocol):
    def supports(self, fixture_type: str) -> bool: ...

    async def run_case(self, spec: EvalCaseSpec) -> CaseRunResult: ...


class ContractCaseRunner:
    _SUPPORTED_TYPES = frozenset(
        {"executor_contract", "state_machine", "context_builder", "privacy_gate"}
    )

    def __init__(self, *, registry: ToolRegistryConfig) -> None:
        self._registry = registry

    def supports(self, fixture_type: str) -> bool:
        return fixture_type in self._SUPPORTED_TYPES

    async def run_case(self, spec: EvalCaseSpec) -> CaseRunResult:
        if not self.supports(spec.fixture.type):
            raise ValueError(
                f"ContractCaseRunner does not support fixture type: {spec.fixture.type}"
            )
        with tempfile.TemporaryDirectory(prefix="he-") as temporary:
            base = Path(temporary)
            workspace = base / "w"
            workspace.mkdir()
            outside = base / "o"
            outside.mkdir()
            (outside / "secret.txt").write_text("synthetic", encoding="utf-8")
            stimulus = spec.fixture.stimulus
            if "workspace_setup" in stimulus:
                (workspace / "link-outside").symlink_to(outside, target_is_directory=True)

            if spec.fixture.type == "context_builder":
                evidence = _run_context_builder_contract()
                results: tuple[ToolResult, ...] = ()
            elif spec.fixture.id == "final_step_has_no_tools":
                evidence = await _run_final_step_contract(base, workspace, spec.seed)
                results = evidence.tool_results
            elif spec.fixture.type in {"privacy_gate", "state_machine"}:
                calls = _fixture_calls(spec.fixture)
                executor = WebToolExecutor(
                    registry=self._registry,
                    session_policy=SessionPolicy(conversation_id="eval"),
                )
                results = tuple([await executor.execute(call) for call in calls])
                effective_calls = () if spec.fixture.type == "privacy_gate" else calls
                evidence = _evidence(
                    workspace=workspace,
                    calls=calls,
                    effective_calls=effective_calls,
                    results=results,
                )
            else:
                calls = _fixture_calls(spec.fixture)
                executor = RegistryToolExecutor(
                    registry=self._registry,
                    workspace_root=workspace,
                    session_policy=_contract_policy(),
                )
                results = tuple([await executor.execute(call) for call in calls])
                evidence = _evidence(
                    workspace=workspace,
                    calls=calls,
                    effective_calls=calls,
                    results=results,
                )
            evaluation = evaluate_oracle(
                spec.fixture.oracle.typed_assertions,
                evidence,
                tool_effects=self._registry.effects_by_tool,
            )
            return CaseRunResult(
                evidence=evidence,
                metrics={},
                security_violations=security_violations(spec.fixture, evaluation),
                evaluation=evaluation,
            )


def _contract_policy() -> SessionPolicy:
    now = datetime.now(UTC)
    return SessionPolicy(
        conversation_id="eval",
        grants=(
            Grant(
                id="eval-root",
                conversation_id="eval",
                permission="WorkspaceRootGrant",
                scope="workspace",
                granted_at=now,
            ),
            Grant(
                id="eval-write",
                conversation_id="eval",
                permission="WriteGrant",
                scope="workspace",
                granted_at=now,
            ),
        ),
    )


def _fixture_calls(fixture: RegressionFixture) -> tuple[ToolCall, ...]:
    stimulus = fixture.stimulus
    raw_call = stimulus.get("tool_call")
    if isinstance(raw_call, Mapping):
        return (_tool_call(cast(Mapping[str, JsonValue], raw_call), 0),)
    attempts = stimulus.get("attempts")
    if attempts is None:
        attempts = stimulus.get("generated_calls")
    if isinstance(attempts, Sequence) and not isinstance(attempts, str):
        calls: list[ToolCall] = []
        for index, raw_attempt in enumerate(attempts):
            if not isinstance(raw_attempt, Mapping):
                raise ValueError(f"invalid attempt in fixture: {fixture.id}")
            attempt = cast(Mapping[str, JsonValue], raw_attempt)
            name = attempt.get("tool")
            if name is None:
                name = attempt.get("name")
            if not isinstance(name, str):
                raise ValueError(f"fixture attempt has no tool: {fixture.id}")
            raw_arguments = attempt.get("arguments")
            arguments = (
                cast(Mapping[str, JsonValue], raw_arguments)
                if isinstance(raw_arguments, Mapping)
                else {key: value for key, value in attempt.items() if key not in {"tool", "name"}}
            )
            calls.append(ToolCall(id=f"eval-{index}", name=name, arguments=arguments))
        return tuple(calls)
    raise ValueError(f"deterministic fixture has no executable tool call: {fixture.id}")


def _evidence(
    *,
    workspace: Path,
    calls: tuple[ToolCall, ...],
    effective_calls: tuple[ToolCall, ...],
    results: tuple[ToolResult, ...],
) -> EvalEvidence:
    blocked = next(
        (result for result in results if result.status is ToolResultStatus.BLOCKED),
        None,
    )
    reason = _result_error_code(blocked) if blocked is not None else None
    return EvalEvidence(
        tool_calls=effective_calls,
        tool_results=results,
        terminal_outcome_kind=(TerminalOutcomeKind.BLOCKED if blocked is not None else None),
        terminal_outcome_reason=reason,
        workspace_root=workspace,
        observed_paths=_observed_paths(workspace, calls),
    )


async def _run_final_step_contract(base: Path, workspace: Path, seed: int) -> EvalEvidence:
    store = ConversationStore(base / "c.sqlite3")
    await store.initialize()
    revision = await store.create_workspace(str(workspace))
    conversation = await store.create_conversation(revision.workspace_id)
    await store.enqueue_request(conversation.id, "synthetic final-step contract")
    runtime = _FinalStepRuntime()
    engine = AgentEngine(
        store=store,
        runtime=runtime,
        tool_executor=_NoopToolExecutor(),
        context_builder=ContextBuilder(
            _DeterministicEstimator(), context_window=128, output_budget=32
        ),
        system_prompt="contract",
        event_sink=NullEventSink(),
        tool_schemas=(ToolSchema(name="contract_tool", description="contract", parameters={}),),
        seed=seed,
        model_options={},
        max_model_invocations=1,
        runtime_readiness=EngineReadiness(ready=True),
    )
    turn = await engine.start_next_turn(conversation.id)
    if turn is None or turn.terminal_outcome is None:
        raise RuntimeError("final-step contract did not produce a TerminalOutcome")
    steps = await store.list_agent_steps(turn.id)
    return EvalEvidence(
        tool_calls=tuple(call for step in steps for call in step.tool_calls),
        tool_results=tuple(result for step in steps for result in step.tool_results),
        terminal_outcome_kind=turn.terminal_outcome.kind,
        terminal_outcome_reason=turn.terminal_outcome.reason_code,
        workspace_root=workspace,
    )


def _run_context_builder_contract() -> EvalEvidence:
    now = datetime.now(UTC)
    entries = (
        CanonicalHistoryEntry(
            id="result-1",
            sequence=1,
            conversation_id="eval",
            turn_id="turn",
            kind=CanonicalHistoryEntryKind.TOOL_RESULT,
            payload={
                "tool_call_id": "call-1",
                "status": "success",
                "retryable": False,
                "data": {"value": "synthetic"},
                "error": None,
                "meta": {"producer": "eval", "truncated": False, "taints": []},
            },
            created_at=now,
        ),
        CanonicalHistoryEntry(
            id="rejected-1",
            sequence=2,
            conversation_id="eval",
            turn_id="turn",
            kind=CanonicalHistoryEntryKind.REJECTED_MODEL_ATTEMPT,
            payload={"code": "synthetic_rejection"},
            created_at=now,
        ),
        CanonicalHistoryEntry(
            id="result-2",
            sequence=3,
            conversation_id="eval",
            turn_id="turn",
            kind=CanonicalHistoryEntryKind.TOOL_RESULT,
            payload={
                "tool_call_id": "call-2",
                "status": "success",
                "retryable": False,
                "data": {"value": "synthetic"},
                "error": None,
                "meta": {"producer": "eval", "truncated": False, "taints": []},
            },
            created_at=now,
        ),
    )
    builder = ContextBuilder(_DeterministicEstimator(), context_window=128, output_budget=32)
    _ = builder.build(
        system="contract",
        tool_schemas=(),
        completed_turns=(),
        current_turn=ContextTurn(turn_id="turn", entries=entries),
    )
    return EvalEvidence()


class _DeterministicEstimator:
    validated = True

    def estimate(self, messages: Sequence[ModelMessage], tools: Sequence[ToolSchema]) -> int:
        return len(messages) + len(tools)


class _FinalStepRuntime:
    async def generate(self, request: ModelRequest) -> ModelResponse:
        if request.tools:
            raise RuntimeError("AgentEngine offered tools on the final step")
        return ModelResponse(content="synthetic final response")


class _NoopToolExecutor:
    async def preflight(self, calls: Sequence[ToolCall]) -> ToolBatchPreflight:
        del calls
        return ToolBatchPreflight(allowed=True)

    async def execute(self, call: ToolCall) -> ToolResult:
        raise RuntimeError(f"unexpected final-step tool execution: {call.name}")

    async def preview(self, call: ToolCall) -> ConfirmationPreview | None:
        del call
        return None


def _tool_call(raw: Mapping[str, JsonValue], index: int) -> ToolCall:
    name = raw.get("name")
    arguments = raw.get("arguments")
    if not isinstance(name, str) or not isinstance(arguments, Mapping):
        raise ValueError("fixture tool_call requires name and arguments")
    return ToolCall(
        id=f"eval-{index}",
        name=name,
        arguments=cast(Mapping[str, JsonValue], arguments),
    )


def _observed_paths(workspace: Path, calls: Sequence[ToolCall]) -> tuple[Path, ...]:
    paths: list[Path] = []
    for call in calls:
        raw = call.arguments.get("file_path")
        if isinstance(raw, str):
            paths.append(Path(raw) if Path(raw).is_absolute() else workspace / raw)
    return tuple(paths)


def _result_error_code(result: ToolResult | None) -> str | None:
    if result is None or result.error is None:
        return None
    code = result.error.get("code")
    return code if isinstance(code, str) else None


_SECURITY_TAGS = frozenset(
    {"security", "privacy", "web_access_grant", "web_taint", "symlink", "eval_bench"}
)


def security_violations(fixture: RegressionFixture, evaluation: OracleEvaluation | None) -> int:
    """Counts security expectations the case failed to meet.

    A blocked call is the harness doing its job, not a violation: half the corpus
    exists precisely to trigger a refusal, and counting refusals would make the
    zero_violations promotion gate impossible to pass by construction. What a
    violation means is that a security-relevant fixture did not get the outcome
    its oracle demands — the attempt went through, or the refusal was the wrong one.
    """
    if evaluation is None or not _SECURITY_TAGS.intersection(fixture.tags):
        return 0
    return int(evaluation.verdict is TaskVerdict.FAIL)
