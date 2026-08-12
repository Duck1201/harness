"""The executor behind the ``corpus_read`` effect.

The third executor, and it exists for the same reason the other two do: the gate
lives with whoever performs the effect. A CorpusGrant names the Corpus it
authorizes in its scope, so this executor does not choose which acervo to read —
it reads the one the Operator granted, and refuses when none was.
"""

from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Any, cast

from jsonschema import Draft202012Validator, FormatChecker

from .config import ToolDefinitionConfig, ToolRegistryConfig
from .corpus_service import CorpusLibraryError, CorpusRetriever
from .domain import (
    CORPUS_EFFECT,
    CORPUS_GRANT,
    Grant,
    JsonValue,
    SessionPolicy,
    ToolCall,
    ToolResult,
    ToolResultStatus,
    grant_reason_code,
)
from .ports import ConfirmationPreview, ToolBatchPreflight

_PRODUCER = "corpus"


class _PreflightIssue(Exception):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


def granted_corpus_id(policy: SessionPolicy) -> str | None:
    """Which Corpus this Conversation may read, from the grant itself.

    The selection and its authorization are one fact. A second place to store
    "which corpus is on" would be a second place to disagree with the grant that
    the policy actually enforces.
    """
    grants: list[Grant] = [
        grant
        for grant in policy.grants
        if grant.permission == CORPUS_GRANT and grant.conversation_id == policy.conversation_id
    ]
    if not grants or CORPUS_GRANT not in policy.effective_grants:
        return None
    newest = max(grants, key=lambda grant: grant.granted_at)
    return newest.scope or None


class CorpusToolExecutor:
    def __init__(
        self,
        *,
        registry: ToolRegistryConfig,
        session_policy: SessionPolicy,
        retriever: CorpusRetriever,
    ) -> None:
        self._registry = {
            definition.name: definition
            for definition in registry.model_tools
            if CORPUS_EFFECT in definition.effects
        }
        self._policy = session_policy
        self._effective_grants = session_policy.effective_grants
        self._retriever = retriever

    async def preflight(self, calls: Sequence[ToolCall]) -> ToolBatchPreflight:
        seen_ids: set[str] = set()
        for raw_call in calls:
            call = self._normalized(raw_call)
            try:
                if not call.id or call.id in seen_ids:
                    raise _PreflightIssue(
                        "duplicate_tool_call_id",
                        "Tool call IDs must be non-empty and unique.",
                    )
                seen_ids.add(call.id)
                self._validate_call(call)
            except _PreflightIssue as issue:
                return ToolBatchPreflight(
                    allowed=False,
                    reason_code=issue.code,
                    detail=issue.detail,
                )
        return ToolBatchPreflight(allowed=True)

    async def execute(self, call: ToolCall) -> ToolResult:
        call = self._normalized(call)
        try:
            self._validate_call(call)
            corpus_id = self._granted_corpus()
        except _PreflightIssue as issue:
            return _error(call, ToolResultStatus.BLOCKED, issue.code, issue.detail)
        query = cast(str, call.arguments["query"])
        limit = call.arguments.get("limit")
        try:
            retrieval = await self._retriever.retrieve(
                corpus_id,
                query,
                # A busca do modelo já chega numa língua só: ele escreveu a query,
                # então ela serve às duas pernas sem reescrita nenhuma.
                lexical_query=query,
                limit=int(limit) if isinstance(limit, int) else None,
            )
        except CorpusLibraryError as error:
            return _error(call, ToolResultStatus.BLOCKED, error.code, str(error))
        status = ToolResultStatus.SUCCESS if retrieval.chunks else ToolResultStatus.EMPTY
        return ToolResult(
            tool_call_id=call.id,
            status=status,
            retryable=False,
            data=retrieval.payload(),
            error=None,
            meta={
                "producer": _PRODUCER,
                "truncated": False,
                "taints": list(retrieval.taints),
                "corpus_id": retrieval.corpus_id,
                "passages": len(retrieval.chunks),
            },
        )

    async def preview(self, call: ToolCall) -> ConfirmationPreview | None:
        # Ler um Corpus não altera nada, então não há diff para o Operator ver.
        del call
        return None

    def _granted_corpus(self) -> str:
        corpus_id = granted_corpus_id(self._policy)
        if corpus_id is None:
            raise _PreflightIssue(
                grant_reason_code(CORPUS_GRANT),
                "No Corpus is selected for this conversation.",
            )
        return corpus_id

    def _normalized(self, call: ToolCall) -> ToolCall:
        definition = self._registry.get(call.name)
        if definition is None:
            return call
        arguments = definition.normalized_arguments(call.arguments)
        return call if arguments == call.arguments else replace(call, arguments=arguments)

    def _validate_call(self, call: ToolCall) -> ToolDefinitionConfig:
        definition = self._registry.get(call.name)
        if definition is None:
            raise _PreflightIssue("unknown_tool", "Unknown corpus tool.")
        if definition.status != "enabled":
            raise _PreflightIssue("tool_not_enabled", "The requested tool is not enabled.")
        validator = Draft202012Validator(
            cast(Mapping[str, Any], definition.parameters),
            format_checker=FormatChecker(),
        )
        errors = sorted(
            validator.iter_errors(call.arguments),  # pyright: ignore[reportUnknownMemberType]
            key=lambda error: list(error.path),
        )
        if errors:
            raise _PreflightIssue("invalid_tool_arguments", errors[0].message)
        if not cast(str, call.arguments.get("query", "")).strip():
            raise _PreflightIssue("invalid_tool_arguments", "The search query must not be blank.")
        # Por efeito, nunca por nome: o registry diz quais grants os efeitos
        # declarados exigem, e a lista abaixo é a mesma dos outros executores.
        missing = [
            grant for grant in definition.required_grants if grant not in self._effective_grants
        ]
        if missing:
            raise _PreflightIssue(
                grant_reason_code(missing[0]),
                f"The {missing[0]} is required for this effect.",
            )
        return definition


def _error(call: ToolCall, status: ToolResultStatus, code: str, message: str) -> ToolResult:
    error: Mapping[str, JsonValue] = {"code": code, "message": message}
    return ToolResult(
        tool_call_id=call.id,
        status=status,
        retryable=False,
        data=None,
        error=error,
        meta={"producer": "harness", "truncated": False, "taints": []},
    )


__all__ = ["CorpusToolExecutor", "granted_corpus_id"]
