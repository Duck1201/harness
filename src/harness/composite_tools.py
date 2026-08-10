import asyncio
from collections.abc import Mapping, Sequence

from .domain import ToolCall, ToolResult, ToolResultStatus
from .ports import ToolBatchPreflight, ToolExecutor


class CompositeToolExecutor:
    def __init__(self, *, routes: Mapping[str, ToolExecutor]) -> None:
        if not routes:
            raise ValueError("Composite routes must not be empty.")
        self._routes = dict(routes)
        self._approved: dict[str, ToolCall] = {}
        self._approved_order: list[str] = []

    async def preflight(self, calls: Sequence[ToolCall]) -> ToolBatchPreflight:
        self._approved.clear()
        self._approved_order.clear()
        children: list[ToolExecutor] = []
        for executor in self._routes.values():
            if all(executor is not existing for existing in children):
                children.append(executor)

        batches: list[list[ToolCall]] = [[] for _ in children]
        issue: ToolBatchPreflight | None = None
        seen_ids: set[str] = set()
        for call in calls:
            if not call.id or call.id in seen_ids:
                issue = issue or ToolBatchPreflight(
                    allowed=False,
                    reason_code="duplicate_tool_call_id",
                    detail="Tool call IDs must be non-empty and unique.",
                )
            seen_ids.add(call.id)
            executor = self._routes.get(call.name)
            if executor is None:
                issue = issue or ToolBatchPreflight(
                    allowed=False,
                    reason_code="unknown_tool",
                    detail="No executor is registered for the requested tool.",
                )
                continue
            child_index = next(
                index for index, child in enumerate(children) if child is executor
            )
            batches[child_index].append(call)

        results = await asyncio.gather(
            *(child.preflight(tuple(batch)) for child, batch in zip(children, batches, strict=True))
        )
        if issue is not None:
            return issue
        for result in results:
            if not result.allowed:
                return result
        self._approved = {call.id: call for call in calls}
        self._approved_order = [call.id for call in calls]
        return ToolBatchPreflight(allowed=True)

    async def execute(self, call: ToolCall) -> ToolResult:
        approved = self._approved.get(call.id)
        if approved != call:
            return _composite_error(
                call,
                "batch_not_preflighted",
                "The complete tool batch must pass preflight before execution.",
            )
        if not self._approved_order or self._approved_order[0] != call.id:
            return _composite_error(
                call,
                "batch_execution_order_violation",
                "Tool calls must execute in their original batch order.",
            )
        executor = self._routes.get(call.name)
        if executor is None:
            return _composite_error(
                call,
                "unknown_tool",
                "No executor is registered for the requested tool.",
            )
        del self._approved[call.id]
        del self._approved_order[0]
        return await executor.execute(call)


def _composite_error(call: ToolCall, code: str, message: str) -> ToolResult:
    return ToolResult(
        tool_call_id=call.id,
        status=ToolResultStatus.BLOCKED,
        retryable=False,
        data=None,
        error={"code": code, "message": message},
        meta={"producer": "composite", "truncated": False, "taints": []},
    )
