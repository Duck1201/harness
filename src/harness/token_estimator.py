import hashlib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Protocol, cast

from tokenizers import Tokenizer

from .domain import JsonValue
from .ports import EngineReadiness, ModelMessage, TokenEstimator, ToolSchema


class HuggingFaceTokenEstimator(TokenEstimator):
    def __init__(
        self,
        tokenizer_path: str | Path,
        *,
        expected_sha256: str,
        base_overhead: int = 0,
        message_overhead: int = 4,
        schema_overhead: int = 8,
    ) -> None:
        if min(base_overhead, message_overhead, schema_overhead) < 0:
            raise ValueError("token overheads must be non-negative")
        self._tokenizer: Tokenizer | None = None
        self._readiness = EngineReadiness(ready=False, reason_code="tokenizer_file_missing")
        self._base_overhead = base_overhead
        self._message_overhead = message_overhead
        self._schema_overhead = schema_overhead

        path = Path(tokenizer_path)
        if not path.is_file():
            return
        try:
            content = path.read_bytes()
        except OSError:
            self._readiness = EngineReadiness(
                ready=False, reason_code="tokenizer_file_unreadable"
            )
            return
        expected = expected_sha256.removeprefix("sha256:").lower()
        if len(expected) != 64 or any(
            character not in "0123456789abcdef" for character in expected
        ):
            self._readiness = EngineReadiness(
                ready=False, reason_code="tokenizer_expected_digest_invalid"
            )
            return
        if hashlib.sha256(content).hexdigest() != expected:
            self._readiness = EngineReadiness(
                ready=False, reason_code="tokenizer_digest_mismatch"
            )
            return
        try:
            self._tokenizer = cast(
                Tokenizer,
                Tokenizer.from_str(  # pyright: ignore[reportUnknownMemberType]
                    content.decode("utf-8")
                ),
            )
        except Exception:
            self._readiness = EngineReadiness(
                ready=False, reason_code="tokenizer_file_invalid"
            )
            return
        self._readiness = EngineReadiness(ready=True)

    @property
    def validated(self) -> bool:
        return self._readiness.ready

    @property
    def readiness(self) -> EngineReadiness:
        return self._readiness

    def estimate(
        self, messages: Sequence[ModelMessage], tools: Sequence[ToolSchema]
    ) -> int:
        tokenizer = self._tokenizer
        if tokenizer is None:
            raise RuntimeError("token estimator is not validated")
        total = self._base_overhead
        for message in messages:
            total += self._message_overhead + _encoded_length(
                tokenizer, _message_payload(message)
            )
        for schema in tools:
            total += self._schema_overhead + _encoded_length(
                tokenizer, _schema_payload(schema)
            )
        return total


def _encoded_length(tokenizer: Tokenizer, payload: Mapping[str, JsonValue]) -> int:
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    encoding = cast(
        _Encoding,
        tokenizer.encode(  # pyright: ignore[reportUnknownMemberType]
            serialized, add_special_tokens=False
        ),
    )
    return len(encoding.ids)


class _Encoding(Protocol):
    @property
    def ids(self) -> list[int]: ...


def _message_payload(message: ModelMessage) -> Mapping[str, JsonValue]:
    payload: dict[str, JsonValue] = {
        "role": message.role.value,
        "content": message.content,
    }
    if message.tool_calls:
        payload["tool_calls"] = [
            {"id": call.id, "name": call.name, "arguments": call.arguments}
            for call in message.tool_calls
        ]
    if message.tool_call_id is not None:
        payload["tool_call_id"] = message.tool_call_id
    if message.name is not None:
        payload["name"] = message.name
    return payload


def _schema_payload(schema: ToolSchema) -> Mapping[str, JsonValue]:
    return {
        "name": schema.name,
        "description": schema.description,
        "parameters": schema.parameters,
    }
