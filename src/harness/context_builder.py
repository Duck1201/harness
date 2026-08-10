import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import cast

from .domain import (
    CanonicalHistoryEntry,
    CanonicalHistoryEntryKind,
    JsonValue,
    ToolCall,
)
from .ports import EngineReadiness, ModelMessage, ModelRole, TokenEstimator, ToolSchema


class ContextBuilderError(Exception):
    pass


class ContextBudgetExceeded(ContextBuilderError):
    pass


class TokenEstimatorNotValidated(ContextBuilderError):
    pass


@dataclass(frozen=True, slots=True)
class ContextTurn:
    turn_id: str
    entries: tuple[CanonicalHistoryEntry, ...]

    def __post_init__(self) -> None:
        if any(entry.turn_id != self.turn_id for entry in self.entries):
            raise ValueError("context turn contains an entry from another turn")


@dataclass(frozen=True, slots=True)
class ModelContext:
    messages: tuple[ModelMessage, ...]
    tool_schemas: tuple[ToolSchema, ...]
    estimated_input_tokens: int
    output_budget: int
    dropped_turn_ids: tuple[str, ...]
    taints: frozenset[str]


class ContextBuilder:
    def __init__(
        self,
        estimator: TokenEstimator,
        *,
        context_window: int = 24576,
        output_budget: int = 8192,
    ) -> None:
        if context_window <= output_budget:
            raise ValueError("context window must exceed output budget")
        self._estimator = estimator
        self._context_window = context_window
        self._output_budget = output_budget

    @property
    def readiness(self) -> EngineReadiness:
        if not self._estimator.validated:
            return EngineReadiness(ready=False, reason_code="token_estimator_not_validated")
        return EngineReadiness(ready=True)

    def build(
        self,
        *,
        system: str,
        tool_schemas: Sequence[ToolSchema],
        completed_turns: Sequence[ContextTurn],
        current_turn: ContextTurn,
    ) -> ModelContext:
        if not self._estimator.validated:
            raise TokenEstimatorNotValidated("token estimator has not been validated")
        if any(turn.turn_id == current_turn.turn_id for turn in completed_turns):
            raise ValueError("current turn must not also be a completed turn")

        schemas = tuple(tool_schemas)
        remaining = list(completed_turns)
        dropped: list[str] = []
        while True:
            messages = self._render(system, (*remaining, current_turn))
            estimated = self._estimator.estimate(messages, schemas)
            if estimated + self._output_budget <= self._context_window:
                included_turns = (*remaining, current_turn)
                return ModelContext(
                    messages=messages,
                    tool_schemas=schemas,
                    estimated_input_tokens=estimated,
                    output_budget=self._output_budget,
                    dropped_turn_ids=tuple(dropped),
                    taints=_tool_result_taints(included_turns),
                )
            if not remaining:
                raise ContextBudgetExceeded("system, tool schemas, and current turn exceed budget")
            dropped.append(remaining.pop(0).turn_id)

    def _render(
        self, system: str, turns: Sequence[ContextTurn]
    ) -> tuple[ModelMessage, ...]:
        messages = [ModelMessage(role=ModelRole.SYSTEM, content=system)]
        seen_payloads: dict[str, CanonicalHistoryEntry] = {}
        for turn in turns:
            for entry in turn.entries:
                messages.append(_entry_message(entry, seen_payloads))
        return tuple(messages)


def _entry_message(
    entry: CanonicalHistoryEntry,
    seen_payloads: dict[str, CanonicalHistoryEntry],
) -> ModelMessage:
    payload = entry.payload
    if entry.kind is CanonicalHistoryEntryKind.USER_MESSAGE:
        return ModelMessage(role=ModelRole.USER, content=_required_string(payload, "content"))
    if entry.kind is CanonicalHistoryEntryKind.REJECTED_MODEL_ATTEMPT:
        return ModelMessage(role=ModelRole.ASSISTANT, content=_json_text(payload))
    if entry.kind is CanonicalHistoryEntryKind.MODEL_ATTEMPT:
        content = payload.get("content")
        text = content if isinstance(content, str) else _json_text(payload)
        return ModelMessage(
            role=ModelRole.ASSISTANT,
            content=text,
            tool_calls=_tool_calls(payload.get("tool_calls")),
        )
    if entry.kind is CanonicalHistoryEntryKind.TOOL_RESULT:
        rendered = dict(payload)
        data = payload.get("data")
        if data is not None:
            digest = hashlib.sha256(_json_text(data).encode()).hexdigest()
            original = seen_payloads.get(digest)
            if original is None:
                seen_payloads[digest] = entry
            else:
                rendered["data"] = {
                    "$ref": {"entry_id": original.id, "sha256": digest}
                }
        return ModelMessage(
            role=ModelRole.TOOL,
            content=_json_text(rendered),
            tool_call_id=_optional_string(payload, "tool_call_id"),
            name=_optional_string(payload, "tool_name"),
        )
    if entry.kind is CanonicalHistoryEntryKind.FINAL_RESPONSE:
        return ModelMessage(role=ModelRole.ASSISTANT, content=_required_string(payload, "content"))
    if entry.kind is CanonicalHistoryEntryKind.INTERNAL_AUTOMATION:
        return ModelMessage(
            role=ModelRole.TOOL,
            content=_json_text(payload),
            name=_optional_string(payload, "automation_id"),
        )
    raise ContextBuilderError(f"unsupported canonical history entry: {entry.kind}")


def _tool_calls(value: JsonValue) -> tuple[ToolCall, ...]:
    if value is None:
        return ()
    if not isinstance(value, Sequence) or isinstance(value, str):
        raise ContextBuilderError("model attempt tool_calls must be an array")
    calls: list[ToolCall] = []
    for raw_item in value:
        if not isinstance(raw_item, Mapping):
            raise ContextBuilderError("model attempt tool call must be an object")
        item = cast(Mapping[str, JsonValue], raw_item)
        arguments = item.get("arguments", {})
        if not isinstance(arguments, Mapping):
            raise ContextBuilderError("model attempt tool arguments must be an object")
        calls.append(
            ToolCall(
                id=_required_string(item, "id"),
                name=_required_string(item, "name"),
                arguments=cast(Mapping[str, JsonValue], arguments),
                idempotency_key=_optional_string(item, "idempotency_key"),
            )
        )
    return tuple(calls)


def _tool_result_taints(turns: Sequence[ContextTurn]) -> frozenset[str]:
    taints: set[str] = set()
    for turn in turns:
        for entry in turn.entries:
            if entry.kind is not CanonicalHistoryEntryKind.TOOL_RESULT:
                continue
            meta = entry.payload.get("meta")
            if not isinstance(meta, Mapping):
                continue
            values = meta.get("taints")
            if not isinstance(values, Sequence) or isinstance(values, str):
                continue
            taints.update(value for value in values if isinstance(value, str))
    return frozenset(taints)


def _required_string(payload: Mapping[str, JsonValue], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str):
        raise ContextBuilderError(f"canonical history field must be a string: {key}")
    return value


def _optional_string(payload: Mapping[str, JsonValue], key: str) -> str | None:
    value = payload.get(key)
    if value is not None and not isinstance(value, str):
        raise ContextBuilderError(f"canonical history field must be a string or null: {key}")
    return value


def _json_text(value: JsonValue) -> str:
    serialized = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return serialized.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
