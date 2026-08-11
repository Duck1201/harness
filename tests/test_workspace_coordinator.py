import asyncio
from collections.abc import Sequence
from pathlib import Path

from harness import (
    ConversationStore,
    ToolBatchPreflight,
    ToolCall,
    ToolResult,
    ToolResultStatus,
    WorkspaceCoordinator,
    load_config,
)


class ConcurrentExecutor:
    def __init__(self) -> None:
        self.active_mutations = 0
        self.max_active_mutations = 0
        self.active_reads = 0
        self.max_active_reads = 0
        self.read_gate = asyncio.Event()

    async def preflight(self, calls: Sequence[ToolCall]) -> ToolBatchPreflight:
        del calls
        return ToolBatchPreflight(allowed=True)

    async def execute(self, call: ToolCall) -> ToolResult:
        if call.name == "read_file":
            self.active_reads += 1
            self.max_active_reads = max(self.max_active_reads, self.active_reads)
            if self.active_reads == 2:
                self.read_gate.set()
            await asyncio.wait_for(self.read_gate.wait(), timeout=1)
            self.active_reads -= 1
            return _result(call, changed=False)

        self.active_mutations += 1
        self.max_active_mutations = max(self.max_active_mutations, self.active_mutations)
        await asyncio.sleep(0.02)
        self.active_mutations -= 1
        return _result(call, changed=call.arguments.get("changed") is not False)


def _result(call: ToolCall, *, changed: bool) -> ToolResult:
    return ToolResult(
        tool_call_id=call.id,
        status=ToolResultStatus.SUCCESS,
        retryable=False,
        data={"changed": changed},
        error=None,
        meta={"producer": "fake", "truncated": False, "taints": []},
    )


def test_workspace_coordinator_serializes_mutations_across_two_conversations(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        store = ConversationStore(tmp_path / "conversations.sqlite3")
        await store.initialize()
        workspace = await store.create_workspace("shared-workspace")
        conversation_a = await store.create_conversation(workspace.workspace_id)
        conversation_b = await store.create_conversation(workspace.workspace_id)
        coordinator = WorkspaceCoordinator(store, load_config().tool_registry.effects_by_tool)
        executor = ConcurrentExecutor()

        mutation_results = await asyncio.gather(
            coordinator.execute(
                workspace.workspace_id,
                ToolCall(
                    id=conversation_a.id,
                    name="write_file",
                    arguments={"changed": True},
                ),
                executor,
            ),
            coordinator.execute(
                workspace.workspace_id,
                ToolCall(
                    id=conversation_b.id,
                    name="edit",
                    arguments={"changed": True},
                ),
                executor,
            ),
        )

        assert all(result.status is ToolResultStatus.SUCCESS for result in mutation_results)
        assert executor.max_active_mutations == 1
        assert (await store.get_workspace_revision(workspace.workspace_id)).revision == 2

        await coordinator.execute(
            workspace.workspace_id,
            ToolCall(
                id="conversation-a-noop",
                name="write_file",
                arguments={"changed": False},
            ),
            executor,
        )
        assert (await store.get_workspace_revision(workspace.workspace_id)).revision == 2

        await asyncio.gather(
            coordinator.execute(
                workspace.workspace_id,
                ToolCall(id="conversation-a-read", name="read_file", arguments={}),
                executor,
            ),
            coordinator.execute(
                workspace.workspace_id,
                ToolCall(id="conversation-b-read", name="read_file", arguments={}),
                executor,
            ),
        )
        assert executor.max_active_reads == 2

    asyncio.run(scenario())
