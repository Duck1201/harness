import asyncio
from collections.abc import Mapping, Sequence

from .conversation_store import ConversationStore
from .domain import ToolCall, ToolResult, ToolResultStatus
from .ports import ToolExecutor

_MUTATION_EFFECT = "workspace_write"


class WorkspaceCoordinator:
    def __init__(self, store: ConversationStore, effects: Mapping[str, Sequence[str]]) -> None:
        self._store = store
        self._effects = effects
        self._mutation_locks: dict[str, asyncio.Lock] = {}

    async def execute(
        self,
        workspace_id: str,
        call: ToolCall,
        executor: ToolExecutor,
    ) -> ToolResult:
        if _MUTATION_EFFECT not in self._effects.get(call.name, ()):
            return await executor.execute(call)

        lock = self._mutation_locks.setdefault(workspace_id, asyncio.Lock())
        async with lock:
            revision = await self._store.get_workspace_revision(workspace_id)
            result = await executor.execute(call)
            if _changed_successfully(result):
                await self._store.advance_workspace_revision(
                    workspace_id, expected_revision=revision.revision
                )
            return result


def _changed_successfully(result: ToolResult) -> bool:
    return (
        result.status is ToolResultStatus.SUCCESS
        and isinstance(result.data, Mapping)
        and result.data.get("changed") is True
    )
