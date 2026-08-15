from collections.abc import Mapping, Sequence
from datetime import UTC, datetime

import pytest

from harness import (
    CanonicalHistoryEntry,
    CanonicalHistoryEntryKind,
    ContextBudgetExceeded,
    ContextBuilder,
    ContextTurn,
    JsonValue,
    ModelMessage,
    ModelRole,
    ToolSchema,
)


class FakeEstimator:
    validated = True

    def estimate(self, messages: Sequence[ModelMessage], tools: Sequence[ToolSchema]) -> int:
        return sum(len(message.content) for message in messages) + (len(tools) * 5)


def entry(
    sequence: int,
    turn_id: str,
    kind: CanonicalHistoryEntryKind,
    payload: Mapping[str, JsonValue],
) -> CanonicalHistoryEntry:
    return CanonicalHistoryEntry(
        id=f"entry-{sequence}",
        sequence=sequence,
        conversation_id="conversation-1",
        turn_id=turn_id,
        kind=kind,
        payload=payload,
        created_at=datetime(2026, 8, 10, tzinfo=UTC),
    )


def test_context_deduplicates_only_result_data_and_preserves_provenance() -> None:
    duplicate_data = {"content": "same payload"}
    previous = ContextTurn(
        turn_id="turn-1",
        entries=(
            entry(1, "turn-1", CanonicalHistoryEntryKind.USER_MESSAGE, {"content": "first"}),
            entry(
                2,
                "turn-1",
                CanonicalHistoryEntryKind.TOOL_RESULT,
                {
                    "tool_call_id": "call-1",
                    "status": "success",
                    "retryable": False,
                    "data": duplicate_data,
                    "error": None,
                    "meta": {"producer": "first", "taints": []},
                },
            ),
        ),
    )
    current = ContextTurn(
        turn_id="turn-2",
        entries=(
            entry(3, "turn-2", CanonicalHistoryEntryKind.USER_MESSAGE, {"content": "again"}),
            entry(
                4,
                "turn-2",
                CanonicalHistoryEntryKind.TOOL_RESULT,
                {
                    "tool_call_id": "call-2",
                    "status": "success",
                    "retryable": True,
                    "data": duplicate_data,
                    "error": None,
                    "meta": {"producer": "second", "taints": ["UntrustedWebTaint"]},
                },
            ),
        ),
    )

    context = ContextBuilder(FakeEstimator(), context_window=32768).build(
        system="system",
        tool_schemas=(ToolSchema("read_file", "read", {"type": "object"}),),
        completed_turns=(previous,),
        current_turn=current,
    )

    tool_messages = [message for message in context.messages if message.role is ModelRole.TOOL]
    first = tool_messages[0].content
    duplicate = tool_messages[1].content
    assert first.startswith("<tool_result>") and first.endswith("</tool_result>")
    assert "<data><content>same payload</content></data>" in first
    assert '<data><entry key="$ref">' in duplicate
    assert "<entry_id>entry-2</entry_id>" in duplicate
    assert "<tool_call_id>call-2</tool_call_id>" in duplicate
    assert "<retryable>true</retryable>" in duplicate
    assert (
        "<meta><producer>second</producer><taints><item>UntrustedWebTaint</item></taints></meta>"
        in duplicate
    )
    assert context.output_budget == 8192
    assert context.tool_schemas[0].name == "read_file"
    assert context.taints == frozenset({"UntrustedWebTaint"})


def test_context_cuts_oldest_complete_turn_and_keeps_current_turn() -> None:
    old = ContextTurn(
        turn_id="old",
        entries=(
            entry(1, "old", CanonicalHistoryEntryKind.USER_MESSAGE, {"content": "123456789"}),
            entry(2, "old", CanonicalHistoryEntryKind.FINAL_RESPONSE, {"content": "abcdefghi"}),
        ),
    )
    current = ContextTurn(
        turn_id="current",
        entries=(
            entry(3, "current", CanonicalHistoryEntryKind.USER_MESSAGE, {"content": "current"}),
        ),
    )

    context = ContextBuilder(FakeEstimator(), context_window=8210).build(
        system="s",
        tool_schemas=(),
        completed_turns=(old,),
        current_turn=current,
    )

    assert context.dropped_turn_ids == ("old",)
    assert [(message.role, message.content) for message in context.messages] == [
        (ModelRole.SYSTEM, "s"),
        (ModelRole.USER, "current"),
    ]
    assert context.taints == frozenset()


def test_tool_result_xml_escapes_markup_and_stays_compact() -> None:
    body = "<script>alert('x' & 'y')</script>" + ("a" * 12000)
    current = ContextTurn(
        turn_id="turn-1",
        entries=(
            entry(1, "turn-1", CanonicalHistoryEntryKind.USER_MESSAGE, {"content": "fetch"}),
            entry(
                2,
                "turn-1",
                CanonicalHistoryEntryKind.TOOL_RESULT,
                {
                    "tool_call_id": "call-1",
                    "tool_name": "web_fetch",
                    "status": "success",
                    "retryable": False,
                    "data": {"content": body},
                    "error": None,
                    "meta": {"producer": "web_fetch", "taints": ["UntrustedWebTaint"]},
                },
            ),
        ),
    )

    context = ContextBuilder(FakeEstimator(), context_window=32768).build(
        system="system",
        tool_schemas=(),
        completed_turns=(),
        current_turn=current,
    )

    rendered = context.messages[-1].content
    assert "<script>" not in rendered
    assert "&lt;script&gt;alert('x' &amp; 'y')&lt;/script&gt;" in rendered
    # A framing that indented or repeated the payload would blow the input budget on one fetch.
    assert context.dropped_turn_ids == ()
    # O denominador do consumo viaja no próprio contexto: quem projeta a barra do
    # painel ou grava telemetria não tem outro caminho até a janela.
    assert context.context_window == 32768
    assert context.estimated_input_tokens < context.context_window
    assert len(rendered) < len(body) + 400


def test_context_raises_when_current_turn_cannot_fit() -> None:
    current = ContextTurn(
        turn_id="current",
        entries=(
            entry(
                1,
                "current",
                CanonicalHistoryEntryKind.USER_MESSAGE,
                {"content": "too large for current budget"},
            ),
        ),
    )

    with pytest.raises(ContextBudgetExceeded):
        ContextBuilder(FakeEstimator(), context_window=8200).build(
            system="system",
            tool_schemas=(),
            completed_turns=(),
            current_turn=current,
        )
