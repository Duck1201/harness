import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from pydantic import TypeAdapter

from ..domain import TerminalOutcomeKind, ToolCall, ToolResult, effective_tool_calls
from .models import (
    FileContentContains,
    FileContentEquals,
    FileExists,
    MaxToolCalls,
    PathWithinWorkspace,
    ResponseLanguagePt,
    ResultDataContains,
    ResultErrorCodeIs,
    ResultProducerIs,
    ResultStatusIs,
    TaskVerdict,
    TerminalOutcomeIs,
    ToolCalled,
    ToolNotCalled,
    TypedAssertion,
)


class LanguageDetector(Protocol):
    @property
    def deterministic(self) -> bool: ...

    def is_portuguese(self, text: str) -> bool: ...


@dataclass(frozen=True, slots=True)
class EvalEvidence:
    tool_calls: tuple[ToolCall, ...] = ()
    tool_results: tuple[ToolResult, ...] = ()
    terminal_outcome_kind: TerminalOutcomeKind | None = None
    terminal_outcome_reason: str | None = None
    # engine_error carries the exception in detail and nowhere else. Without it a
    # crash observed once in eighty-one cases leaves nothing to diagnose.
    terminal_outcome_detail: str | None = None
    workspace_root: Path | None = None
    observed_paths: tuple[Path, ...] = ()
    response: str | None = None


@dataclass(frozen=True, slots=True)
class AssertionEvaluation:
    operator: str
    verdict: TaskVerdict
    explanation: str


@dataclass(frozen=True, slots=True)
class OracleEvaluation:
    verdict: TaskVerdict
    assertions: tuple[AssertionEvaluation, ...]
    terminal_outcome_kind: TerminalOutcomeKind | None
    terminal_outcome_reason: str | None


_ASSERTION_ADAPTER: TypeAdapter[TypedAssertion] = TypeAdapter(TypedAssertion)


def evaluate_oracle(
    assertions: Sequence[TypedAssertion | Mapping[str, object]],
    evidence: EvalEvidence,
    *,
    language_detector: LanguageDetector | None = None,
) -> OracleEvaluation:
    evaluations = [
        _evaluate(_parse_assertion(assertion), evidence, language_detector)
        for assertion in assertions
    ]
    verdicts = {item.verdict for item in evaluations}
    if TaskVerdict.FAIL in verdicts:
        verdict = TaskVerdict.FAIL
    elif TaskVerdict.INCONCLUSIVE in verdicts:
        verdict = TaskVerdict.INCONCLUSIVE
    elif evaluations:
        verdict = TaskVerdict.PASS
    else:
        verdict = TaskVerdict.NOT_EVALUATED
    return OracleEvaluation(
        verdict=verdict,
        assertions=tuple(evaluations),
        terminal_outcome_kind=evidence.terminal_outcome_kind,
        terminal_outcome_reason=evidence.terminal_outcome_reason,
    )


def _parse_assertion(value: TypedAssertion | Mapping[str, object]) -> TypedAssertion:
    if isinstance(value, Mapping):
        return _ASSERTION_ADAPTER.validate_python(value)
    return value


def _evaluate(
    assertion: TypedAssertion,
    evidence: EvalEvidence,
    detector: LanguageDetector | None,
) -> AssertionEvaluation:
    passed: bool | None
    detail: str
    if isinstance(assertion, ToolCalled):
        passed = any(call.name == assertion.tool for call in evidence.tool_calls)
        detail = f"tool {assertion.tool} was called"
    elif isinstance(assertion, ToolNotCalled):
        passed = all(call.name != assertion.tool for call in evidence.tool_calls)
        detail = f"tool {assertion.tool} was not called"
    elif isinstance(assertion, MaxToolCalls):
        # The Turn's budget ignores a repeat that came back byte-identical, and the
        # oracle has to count the same way or it would judge a call the harness
        # never charged for.
        observed = len(effective_tool_calls(evidence.tool_calls, evidence.tool_results))
        passed = observed <= assertion.maximum
        detail = f"observed {observed} effective tool calls, maximum {assertion.maximum}"
    elif isinstance(assertion, TerminalOutcomeIs):
        passed = evidence.terminal_outcome_kind is assertion.kind and (
            assertion.reason_code is None
            or evidence.terminal_outcome_reason == assertion.reason_code
        )
        detail = f"terminal outcome is {assertion.kind.value}"
    elif isinstance(assertion, ResultStatusIs):
        results = _selected_results(evidence, assertion.tool_call_id)
        passed = bool(results) and all(result.status is assertion.status for result in results)
        detail = f"result status is {assertion.status.value}"
    elif isinstance(assertion, ResultErrorCodeIs):
        results = _selected_results(evidence, assertion.tool_call_id)
        passed = bool(results) and all(
            result.error is not None
            and (
                result.error.get("code") == assertion.code
                or result.error.get("class") == assertion.code
            )
            for result in results
        )
        detail = f"result error code is {assertion.code}"
    elif isinstance(assertion, ResultDataContains):
        results = _selected_results(evidence, assertion.tool_call_id)
        # Any, not all: one call among several answering with the expected finding
        # is what the fixture claims. Requiring every result to contain it would
        # make an extra unrelated call flip a correct run to a failure.
        passed = any(assertion.content in _serialized_data(result) for result in results)
        detail = f"result data contains expected text: {assertion.content}"
    elif isinstance(assertion, ResultProducerIs):
        results = _selected_results(evidence, assertion.tool_call_id)
        passed = bool(results) and all(
            result.meta.get("producer") == assertion.producer for result in results
        )
        detail = f"result producer is {assertion.producer}"
    elif isinstance(assertion, FileExists):
        path = _workspace_path(evidence.workspace_root, assertion.path)
        passed = path is not None and path.is_file()
        detail = f"file exists: {assertion.path}"
    elif isinstance(assertion, FileContentEquals):
        content = _read_workspace_file(evidence.workspace_root, assertion.path)
        passed = content == assertion.content
        detail = f"file content equals expected bytes: {assertion.path}"
    elif isinstance(assertion, FileContentContains):
        content = _read_workspace_file(evidence.workspace_root, assertion.path)
        passed = content is not None and assertion.content in content
        detail = f"file content contains expected text: {assertion.path}"
    elif isinstance(assertion, PathWithinWorkspace):
        path = _workspace_path(evidence.workspace_root, assertion.path)
        passed = path is not None
        if passed and evidence.observed_paths:
            passed = all(
                _is_within(candidate, evidence.workspace_root)
                for candidate in evidence.observed_paths
            )
        detail = f"path remains within workspace: {assertion.path}"
    else:
        assert isinstance(assertion, ResponseLanguagePt)
        if detector is None or not detector.deterministic or evidence.response is None:
            passed = None
        else:
            passed = detector.is_portuguese(evidence.response)
        detail = "response language is Portuguese"
    return AssertionEvaluation(
        operator=assertion.operator,
        verdict=(
            TaskVerdict.INCONCLUSIVE
            if passed is None
            else TaskVerdict.PASS
            if passed
            else TaskVerdict.FAIL
        ),
        explanation=detail,
    )


def _serialized_data(result: ToolResult) -> str:
    return json.dumps(result.data, ensure_ascii=False, sort_keys=True)


def _selected_results(evidence: EvalEvidence, tool_call_id: str | None) -> tuple[ToolResult, ...]:
    if tool_call_id is None:
        return evidence.tool_results
    return tuple(result for result in evidence.tool_results if result.tool_call_id == tool_call_id)


def _workspace_path(root: Path | None, relative: str) -> Path | None:
    if root is None or Path(relative).is_absolute():
        return None
    resolved_root = root.resolve()
    try:
        candidate = (resolved_root / relative).resolve(strict=False)
    except OSError:
        return None
    return candidate if candidate.is_relative_to(resolved_root) else None


def _read_workspace_file(root: Path | None, relative: str) -> str | None:
    path = _workspace_path(root, relative)
    if path is None:
        return None
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None


def _is_within(candidate: Path, root: Path | None) -> bool:
    if root is None:
        return False
    try:
        return candidate.resolve(strict=False).is_relative_to(root.resolve())
    except OSError:
        return False
