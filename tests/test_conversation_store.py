import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest

from harness import (
    ActiveTurnExistsError,
    CanonicalHistoryEntryKind,
    ConversationStore,
    IdempotencyConflictError,
    NotFoundError,
    RequestStatus,
    ResultPayload,
    TerminalOutcomeAlreadySetError,
    TerminalOutcomeKind,
    ToolCall,
    ToolResult,
    ToolResultStatus,
)


def test_queue_is_editable_cancelable_fifo_and_allows_one_active_turn(tmp_path: Path) -> None:
    async def scenario() -> None:
        store = ConversationStore(tmp_path / "conversations.sqlite3")
        await store.initialize()

        workspace = await store.create_workspace("shared-workspace")
        conversation = await store.create_conversation(workspace.workspace_id)
        first = await store.enqueue_request(conversation.id, "first")
        second = await store.enqueue_request(conversation.id, "second")
        third = await store.enqueue_request(conversation.id, "third")

        edited = await store.edit_pending_request(second.id, "second edited")
        canceled = await store.cancel_pending_request(third.id)

        assert edited.content == "second edited"
        assert edited.sequence == second.sequence
        assert canceled.status is RequestStatus.CANCELED
        assert [request.id for request in await store.list_pending_requests(conversation.id)] == [
            first.id,
            second.id,
        ]

        turn = await store.start_next_turn(conversation.id)

        assert turn is not None
        assert turn.request_id == first.id
        assert (await store.get_request(first.id)).status is RequestStatus.DEQUEUED
        with pytest.raises(ActiveTurnExistsError):
            await store.start_next_turn(conversation.id)

    asyncio.run(scenario())


def test_shared_revision_feedback_outbox_and_whole_conversation_retention(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        store = ConversationStore(tmp_path / "conversations.sqlite3")
        await store.initialize()
        workspace = await store.create_workspace("shared")
        inactive = await store.create_conversation(workspace.workspace_id)

        feedback = await store.add_feedback(inactive.id, rating=1, comment="useful")
        assert await store.list_feedback(inactive.id) == [feedback]

        first = await store.append_outbox_event(
            inactive.id,
            event_type="conversation.created",
            payload={"workspace_id": workspace.workspace_id},
            idempotency_key="conversation-created",
        )
        duplicate = await store.append_outbox_event(
            inactive.id,
            event_type="conversation.created",
            payload={"workspace_id": workspace.workspace_id},
            idempotency_key="conversation-created",
        )
        second = await store.append_outbox_event(
            inactive.id,
            event_type="feedback.recorded",
            payload={"rating": 1},
            idempotency_key="feedback-recorded",
        )

        assert duplicate == first
        assert [event.id for event in await store.list_outbox_events()] == [first.id, second.id]
        with pytest.raises(IdempotencyConflictError):
            await store.append_outbox_event(
                inactive.id,
                event_type="feedback.recorded",
                payload={"rating": -1},
                idempotency_key="feedback-recorded",
            )
        await store.mark_outbox_published(first.id)
        assert await store.list_outbox_events(unpublished_only=True) == [second]

        cutoff = datetime.now(UTC)
        retained = await store.create_conversation(workspace.workspace_id)
        revision = await store.advance_workspace_revision(
            workspace.workspace_id, expected_revision=0
        )
        assert revision.revision == 1
        assert await store.get_workspace_revision(retained.workspace_id) == revision
        assert await store.get_workspace_revision(inactive.workspace_id) == revision

        assert await store.purge_inactive(before=cutoff) == [inactive.id]
        with pytest.raises(NotFoundError):
            await store.get_conversation(inactive.id)
        assert await store.get_conversation(retained.id) == retained
        assert (await store.get_workspace_revision(workspace.workspace_id)).revision == 1
        assert [event.conversation_id for event in await store.list_outbox_events()] == [
            inactive.id,
            inactive.id,
        ]

    asyncio.run(scenario())


def test_active_turn_records_grants_steps_and_terminal_outcome_once(tmp_path: Path) -> None:
    async def scenario() -> None:
        store = ConversationStore(tmp_path / "conversations.sqlite3")
        await store.initialize()
        workspace = await store.create_workspace("workspace")
        conversation = await store.create_conversation(workspace.workspace_id)
        first = await store.enqueue_request(conversation.id, "inspect the project")
        second = await store.enqueue_request(conversation.id, "summarize it")
        turn = await store.start_next_turn(conversation.id, base_seed=314159)
        assert turn is not None
        assert turn.base_seed == 314159

        grant = await store.grant(conversation.id, permission="tool:read_file", scope="workspace")
        policy = await store.get_session_policy(conversation.id)
        assert policy.grants == (grant,)

        expired = await store.grant(
            conversation.id,
            permission="tool:web_fetch",
            scope="public-network",
            expires_at=datetime(2000, 1, 1, tzinfo=UTC),
        )
        assert expired not in (await store.get_session_policy(conversation.id)).grants
        renewed = await store.grant(
            conversation.id,
            permission="tool:web_fetch",
            scope="public-network",
        )
        assert renewed.id != expired.id
        assert renewed in (await store.get_session_policy(conversation.id)).grants

        step = await store.append_agent_step(
            turn.id,
            seed=314159,
            tool_calls=(
                ToolCall(
                    id="call-1",
                    name="read_file",
                    arguments={"file_path": "README.md"},
                    idempotency_key="read-readme",
                ),
            ),
            tool_results=(
                ToolResult(
                    tool_call_id="call-1",
                    status=ToolResultStatus.SUCCESS,
                    retryable=False,
                    data={"content": "project"},
                    error=None,
                    meta={"truncated": False},
                ),
            ),
        )
        assert step.sequence == 1
        assert step.seed == 314159
        assert step.tool_results[0].payload == ResultPayload(
            data={"content": "project"}, error=None
        )
        assert await store.list_agent_steps(turn.id) == [step]

        finished = await store.finish_turn(
            turn.id,
            TerminalOutcomeKind.COMPLETED,
            reason_code="final_response",
        )
        assert finished.terminal_outcome is not None
        assert finished.terminal_outcome.kind is TerminalOutcomeKind.COMPLETED
        assert finished.terminal_outcome.reason_code == "final_response"
        with pytest.raises(TerminalOutcomeAlreadySetError):
            await store.finish_turn(
                turn.id,
                TerminalOutcomeKind.FAILED,
                reason_code="unexpected_error",
            )

        next_turn = await store.start_next_turn(conversation.id)
        assert next_turn is not None
        assert next_turn.request_id == second.id
        assert first.id != second.id

    asyncio.run(scenario())


def test_canonical_history_is_typed_ordered_durable_and_excludes_reasoning(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        database = tmp_path / "conversations.sqlite3"
        store = ConversationStore(database)
        await store.initialize()
        workspace = await store.create_workspace("history")
        conversation = await store.create_conversation(workspace.workspace_id)
        request = await store.enqueue_request(conversation.id, "inspect")
        turn = await store.start_next_turn(conversation.id)
        assert turn is not None

        user_entry = await store.append_canonical_history(
            turn.id,
            CanonicalHistoryEntryKind.USER_MESSAGE,
            {"content": request.content},
        )
        attempt_entry = await store.append_canonical_history(
            turn.id,
            CanonicalHistoryEntryKind.MODEL_ATTEMPT,
            {"content": None, "tool_calls": [{"id": "call-1", "name": "read_file"}]},
        )
        result_entry = await store.append_canonical_history(
            turn.id,
            CanonicalHistoryEntryKind.TOOL_RESULT,
            {
                "tool_call_id": "call-1",
                "status": "failed",
                "retryable": False,
                "data": None,
                "error": {"code": "not_found", "message": "missing"},
                "meta": {"producer": "fake", "truncated": False, "taints": []},
            },
        )

        with pytest.raises(ValueError, match="reasoning"):
            await store.append_canonical_history(
                turn.id,
                CanonicalHistoryEntryKind.REJECTED_MODEL_ATTEMPT,
                {"reasoning": "must remain transient"},
            )

        reopened = ConversationStore(database)
        await reopened.initialize()
        assert await reopened.list_canonical_history(conversation.id) == [
            user_entry,
            attempt_entry,
            result_entry,
        ]
        assert await reopened.list_canonical_history(conversation.id, turn_id=turn.id) == [
            user_entry,
            attempt_entry,
            result_entry,
        ]

    asyncio.run(scenario())


def test_tool_result_requires_a_structured_error() -> None:
    with pytest.raises(TypeError, match="mapping"):
        ToolResult(
            tool_call_id="call-1",
            status=ToolResultStatus.FAILED,
            retryable=False,
            data=None,
            error="plain text",  # type: ignore[arg-type]
            meta={},
        )
