import asyncio
import json
import secrets
import sqlite3
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from uuid import uuid4

from .domain import (
    AgentStep,
    CanonicalHistoryEntry,
    CanonicalHistoryEntryKind,
    Conversation,
    DomainEvent,
    Feedback,
    Grant,
    JsonValue,
    PendingRequest,
    RequestStatus,
    SessionPolicy,
    TerminalOutcome,
    TerminalOutcomeKind,
    ToolCall,
    ToolResult,
    ToolResultStatus,
    Turn,
    TurnStatus,
    WorkspaceRevision,
)


class ConversationStoreError(Exception):
    """Base error for conversation repository operations."""


class NotFoundError(ConversationStoreError):
    pass


class RequestNotPendingError(ConversationStoreError):
    pass


class ActiveTurnExistsError(ConversationStoreError):
    pass


class TurnNotActiveError(ConversationStoreError):
    pass


class TerminalOutcomeAlreadySetError(ConversationStoreError):
    pass


class WorkspaceRevisionConflictError(ConversationStoreError):
    pass


class IdempotencyConflictError(ConversationStoreError):
    pass


class ConversationStore:
    def __init__(self, database: str | Path) -> None:
        self._database = str(database)

    @property
    def database(self) -> str:
        return self._database

    async def initialize(self) -> None:
        await asyncio.to_thread(self._initialize)

    async def create_workspace(self, reference: str) -> WorkspaceRevision:
        return await asyncio.to_thread(self._create_workspace, reference)

    async def create_conversation(
        self, workspace_id: str, *, name: str = "New conversation"
    ) -> Conversation:
        return await asyncio.to_thread(self._create_conversation, workspace_id, name)

    async def get_conversation(self, conversation_id: str) -> Conversation:
        return await asyncio.to_thread(self._get_conversation, conversation_id)

    async def list_conversations(self, *, include_archived: bool = False) -> list[Conversation]:
        return await asyncio.to_thread(self._list_conversations, include_archived)

    async def rename_conversation(self, conversation_id: str, name: str) -> Conversation:
        return await asyncio.to_thread(self._rename_conversation, conversation_id, name)

    async def set_conversation_archived(
        self, conversation_id: str, *, archived: bool
    ) -> Conversation:
        return await asyncio.to_thread(self._set_conversation_archived, conversation_id, archived)

    async def delete_conversation(self, conversation_id: str) -> None:
        await asyncio.to_thread(self._delete_conversation, conversation_id)

    async def get_workspace_revision(self, workspace_id: str) -> WorkspaceRevision:
        return await asyncio.to_thread(self._get_workspace_revision, workspace_id)

    async def advance_workspace_revision(
        self, workspace_id: str, *, expected_revision: int
    ) -> WorkspaceRevision:
        return await asyncio.to_thread(
            self._advance_workspace_revision, workspace_id, expected_revision
        )

    async def enqueue_request(self, conversation_id: str, content: str) -> PendingRequest:
        return await asyncio.to_thread(self._enqueue_request, conversation_id, content)

    async def edit_pending_request(self, request_id: str, content: str) -> PendingRequest:
        return await asyncio.to_thread(self._edit_pending_request, request_id, content)

    async def cancel_pending_request(self, request_id: str) -> PendingRequest:
        return await asyncio.to_thread(self._cancel_pending_request, request_id)

    async def list_pending_requests(self, conversation_id: str) -> list[PendingRequest]:
        return await asyncio.to_thread(self._list_pending_requests, conversation_id)

    async def get_request(self, request_id: str) -> PendingRequest:
        return await asyncio.to_thread(self._get_request, request_id)

    async def start_next_turn(
        self, conversation_id: str, *, base_seed: int | None = None
    ) -> Turn | None:
        if base_seed is not None and (base_seed < 0 or base_seed > 2**63 - 1):
            raise ValueError("base_seed must be a non-negative signed 64-bit integer")
        return await asyncio.to_thread(self._start_next_turn, conversation_id, base_seed)

    async def list_turns(self, conversation_id: str) -> list[Turn]:
        return await asyncio.to_thread(self._list_turns, conversation_id)

    async def recover_stale_active_turns(self) -> list[Turn]:
        return await asyncio.to_thread(self._recover_stale_active_turns)

    async def grant(
        self,
        conversation_id: str,
        permission: str,
        scope: str,
        *,
        expires_at: datetime | None = None,
    ) -> Grant:
        return await asyncio.to_thread(self._grant, conversation_id, permission, scope, expires_at)

    async def get_session_policy(self, conversation_id: str) -> SessionPolicy:
        return await asyncio.to_thread(self._get_session_policy, conversation_id)

    async def revoke_grant(self, conversation_id: str, grant_id: str) -> Grant:
        return await asyncio.to_thread(self._revoke_grant, conversation_id, grant_id)

    async def append_agent_step(
        self,
        turn_id: str,
        *,
        seed: int,
        tool_calls: Sequence[ToolCall] = (),
        tool_results: Sequence[ToolResult] = (),
    ) -> AgentStep:
        return await asyncio.to_thread(
            self._append_agent_step, turn_id, seed, tuple(tool_calls), tuple(tool_results)
        )

    async def list_agent_steps(self, turn_id: str) -> list[AgentStep]:
        return await asyncio.to_thread(self._list_agent_steps, turn_id)

    async def finish_turn(
        self,
        turn_id: str,
        kind: TerminalOutcomeKind,
        *,
        reason_code: str,
        detail: str | None = None,
    ) -> Turn:
        return await asyncio.to_thread(self._finish_turn, turn_id, kind, reason_code, detail)

    async def append_canonical_history(
        self,
        turn_id: str,
        kind: CanonicalHistoryEntryKind,
        payload: Mapping[str, JsonValue],
    ) -> CanonicalHistoryEntry:
        return await asyncio.to_thread(self._append_canonical_history, turn_id, kind, payload)

    async def list_canonical_history(
        self, conversation_id: str, *, turn_id: str | None = None
    ) -> list[CanonicalHistoryEntry]:
        return await asyncio.to_thread(self._list_canonical_history, conversation_id, turn_id)

    async def add_feedback(
        self,
        conversation_id: str,
        *,
        rating: int,
        comment: str | None = None,
        turn_id: str | None = None,
    ) -> Feedback:
        return await asyncio.to_thread(
            self._add_feedback, conversation_id, rating, comment, turn_id
        )

    async def list_feedback(self, conversation_id: str) -> list[Feedback]:
        return await asyncio.to_thread(self._list_feedback, conversation_id)

    async def append_outbox_event(
        self,
        conversation_id: str | None,
        *,
        event_type: str,
        payload: Mapping[str, JsonValue],
        idempotency_key: str,
    ) -> DomainEvent:
        return await asyncio.to_thread(
            self._append_outbox_event,
            conversation_id,
            event_type,
            payload,
            idempotency_key,
        )

    async def list_outbox_events(
        self,
        *,
        after_sequence: int = 0,
        limit: int = 100,
        unpublished_only: bool = False,
    ) -> list[DomainEvent]:
        return await asyncio.to_thread(
            self._list_outbox_events, after_sequence, limit, unpublished_only
        )

    async def mark_outbox_published(self, event_id: str) -> DomainEvent:
        return await asyncio.to_thread(self._mark_outbox_published, event_id)

    async def purge_inactive(self, *, before: datetime) -> list[str]:
        return await asyncio.to_thread(self._purge_inactive, before)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    def _initialize(self) -> None:
        statements = (
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS workspaces (
                id TEXT PRIMARY KEY,
                reference TEXT NOT NULL UNIQUE,
                revision INTEGER NOT NULL DEFAULT 0 CHECK (revision >= 0),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL REFERENCES workspaces(id),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_active_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS pending_requests (
                queue_sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                id TEXT NOT NULL UNIQUE,
                conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                content TEXT NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('queued', 'canceled', 'dequeued')),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
            """
            CREATE INDEX IF NOT EXISTS pending_requests_fifo
            ON pending_requests(conversation_id, queue_sequence)
            WHERE status = 'queued'
            """,
            """
            CREATE TABLE IF NOT EXISTS turns (
                id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                request_id TEXT NOT NULL UNIQUE REFERENCES pending_requests(id),
                status TEXT NOT NULL CHECK (status IN ('active', 'finished')),
                started_at TEXT NOT NULL
            )
            """,
            """
            CREATE UNIQUE INDEX IF NOT EXISTS one_active_turn_per_conversation
            ON turns(conversation_id)
            WHERE status = 'active'
            """,
        )
        with self._connect() as connection:
            connection.execute(statements[0])
            applied = connection.execute(
                "SELECT 1 FROM schema_migrations WHERE version = 1"
            ).fetchone()
            if applied is None:
                for statement in statements[1:]:
                    connection.execute(statement)
                connection.execute(
                    "INSERT INTO schema_migrations(version, applied_at) VALUES (1, ?)",
                    (_serialize_datetime(_utcnow()),),
                )
            applied = connection.execute(
                "SELECT 1 FROM schema_migrations WHERE version = 2"
            ).fetchone()
            if applied is None:
                migration = (
                    "ALTER TABLE turns ADD COLUMN ended_at TEXT",
                    """
                    ALTER TABLE turns ADD COLUMN terminal_outcome_kind TEXT
                    CHECK (terminal_outcome_kind IS NULL OR terminal_outcome_kind IN (
                        'completed', 'limit_reached', 'cancelled',
                        'blocked', 'failed', 'abandoned'
                    ))
                    """,
                    """
                    ALTER TABLE turns ADD COLUMN terminal_reason_code TEXT
                    CHECK (terminal_reason_code IS NULL OR length(terminal_reason_code) > 0)
                    """,
                    "ALTER TABLE turns ADD COLUMN terminal_detail TEXT",
                    """
                    CREATE TRIGGER terminal_outcome_write_once
                    BEFORE UPDATE OF terminal_outcome_kind ON turns
                    WHEN OLD.terminal_outcome_kind IS NOT NULL
                    BEGIN
                        SELECT RAISE(ABORT, 'terminal outcome already set');
                    END
                    """,
                    """
                    CREATE TRIGGER terminal_outcome_requires_reason_code
                    BEFORE UPDATE OF terminal_outcome_kind ON turns
                    WHEN NEW.terminal_outcome_kind IS NOT NULL
                         AND NEW.terminal_reason_code IS NULL
                    BEGIN
                        SELECT RAISE(ABORT, 'terminal outcome requires reason code');
                    END
                    """,
                    """
                    CREATE TABLE grants (
                        id TEXT PRIMARY KEY,
                        conversation_id TEXT NOT NULL
                            REFERENCES conversations(id) ON DELETE CASCADE,
                        permission TEXT NOT NULL,
                        scope TEXT NOT NULL,
                        granted_at TEXT NOT NULL,
                        expires_at TEXT,
                        revoked_at TEXT
                    )
                    """,
                    """
                    CREATE UNIQUE INDEX one_active_grant
                    ON grants(conversation_id, permission, scope)
                    WHERE revoked_at IS NULL
                    """,
                    """
                    CREATE TABLE agent_steps (
                        id TEXT PRIMARY KEY,
                        turn_id TEXT NOT NULL REFERENCES turns(id) ON DELETE CASCADE,
                        sequence INTEGER NOT NULL CHECK (sequence > 0),
                        tool_calls TEXT NOT NULL,
                        tool_results TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        UNIQUE(turn_id, sequence)
                    )
                    """,
                )
                for statement in migration:
                    connection.execute(statement)
                connection.execute(
                    "INSERT INTO schema_migrations(version, applied_at) VALUES (2, ?)",
                    (_serialize_datetime(_utcnow()),),
                )
            applied = connection.execute(
                "SELECT 1 FROM schema_migrations WHERE version = 3"
            ).fetchone()
            if applied is None:
                migration = (
                    """
                    CREATE TABLE feedback (
                        id TEXT PRIMARY KEY,
                        conversation_id TEXT NOT NULL
                            REFERENCES conversations(id) ON DELETE CASCADE,
                        turn_id TEXT REFERENCES turns(id) ON DELETE CASCADE,
                        rating INTEGER NOT NULL CHECK (rating BETWEEN -1 AND 1),
                        comment TEXT,
                        created_at TEXT NOT NULL
                    )
                    """,
                    """
                    CREATE TABLE outbox (
                        sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                        id TEXT NOT NULL UNIQUE,
                        conversation_id TEXT,
                        event_type TEXT NOT NULL,
                        payload TEXT NOT NULL,
                        idempotency_key TEXT NOT NULL UNIQUE,
                        occurred_at TEXT NOT NULL,
                        published_at TEXT
                    )
                    """,
                    """
                    CREATE INDEX outbox_unpublished
                    ON outbox(sequence)
                    WHERE published_at IS NULL
                    """,
                )
                for statement in migration:
                    connection.execute(statement)
                connection.execute(
                    "INSERT INTO schema_migrations(version, applied_at) VALUES (3, ?)",
                    (_serialize_datetime(_utcnow()),),
                )
            applied = connection.execute(
                "SELECT 1 FROM schema_migrations WHERE version = 4"
            ).fetchone()
            if applied is None:
                migration = (
                    """
                    CREATE TABLE canonical_history (
                        sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                        id TEXT NOT NULL UNIQUE,
                        conversation_id TEXT NOT NULL
                            REFERENCES conversations(id) ON DELETE CASCADE,
                        turn_id TEXT NOT NULL REFERENCES turns(id) ON DELETE CASCADE,
                        entry_type TEXT NOT NULL CHECK (entry_type IN (
                            'user_message',
                            'model_attempt',
                            'rejected_model_attempt',
                            'tool_result',
                            'final_response',
                            'internal_automation'
                        )),
                        payload TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    )
                    """,
                    """
                    CREATE INDEX canonical_history_conversation_turn
                    ON canonical_history(conversation_id, turn_id, sequence)
                    """,
                )
                for statement in migration:
                    connection.execute(statement)
                connection.execute(
                    "INSERT INTO schema_migrations(version, applied_at) VALUES (4, ?)",
                    (_serialize_datetime(_utcnow()),),
                )

            applied = connection.execute(
                "SELECT 1 FROM schema_migrations WHERE version = 5"
            ).fetchone()
            if applied is None:
                connection.execute(
                    "ALTER TABLE conversations ADD COLUMN name TEXT NOT NULL "
                    "DEFAULT 'New conversation'"
                )
                connection.execute("ALTER TABLE conversations ADD COLUMN archived_at TEXT")
                connection.execute(
                    "INSERT INTO schema_migrations(version, applied_at) VALUES (5, ?)",
                    (_serialize_datetime(_utcnow()),),
                )

            applied = connection.execute(
                "SELECT 1 FROM schema_migrations WHERE version = 6"
            ).fetchone()
            if applied is None:
                connection.execute(
                    "ALTER TABLE turns ADD COLUMN base_seed INTEGER NOT NULL DEFAULT 0 "
                    "CHECK (base_seed >= 0)"
                )
                connection.execute(
                    "ALTER TABLE agent_steps ADD COLUMN seed INTEGER NOT NULL DEFAULT 0 "
                    "CHECK (seed >= 0)"
                )
                connection.execute(
                    "INSERT INTO schema_migrations(version, applied_at) VALUES (6, ?)",
                    (_serialize_datetime(_utcnow()),),
                )

    def _create_workspace(self, reference: str) -> WorkspaceRevision:
        if not reference:
            raise ValueError("workspace reference must not be empty")
        now = _utcnow()
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT id, revision, updated_at FROM workspaces WHERE reference = ?",
                (reference,),
            ).fetchone()
            if existing is not None:
                return _workspace_revision_from_row(existing)
            workspace_id = str(uuid4())
            connection.execute(
                """
                INSERT INTO workspaces(id, reference, revision, created_at, updated_at)
                VALUES (?, ?, 0, ?, ?)
                """,
                (workspace_id, reference, _serialize_datetime(now), _serialize_datetime(now)),
            )
        return WorkspaceRevision(workspace_id=workspace_id, revision=0, updated_at=now)

    def _create_conversation(self, workspace_id: str, name: str) -> Conversation:
        normalized_name = name.strip()
        if not normalized_name:
            raise ValueError("conversation name must not be blank")
        now = _utcnow()
        conversation = Conversation(
            id=str(uuid4()),
            workspace_id=workspace_id,
            created_at=now,
            updated_at=now,
            last_active_at=now,
            name=normalized_name,
        )
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO conversations(
                        id, workspace_id, created_at, updated_at, last_active_at, name
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        conversation.id,
                        conversation.workspace_id,
                        _serialize_datetime(now),
                        _serialize_datetime(now),
                        _serialize_datetime(now),
                        conversation.name,
                    ),
                )
        except sqlite3.IntegrityError as error:
            raise NotFoundError(f"workspace not found: {workspace_id}") from error
        return conversation

    def _get_conversation(self, conversation_id: str) -> Conversation:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM conversations WHERE id = ?", (conversation_id,)
            ).fetchone()
        if row is None:
            raise NotFoundError(f"conversation not found: {conversation_id}")
        return _conversation_from_row(row)

    def _list_conversations(self, include_archived: bool) -> list[Conversation]:
        query = (
            "SELECT * FROM conversations ORDER BY last_active_at DESC, id"
            if include_archived
            else "SELECT * FROM conversations WHERE archived_at IS NULL "
            "ORDER BY last_active_at DESC, id"
        )
        with self._connect() as connection:
            rows = connection.execute(query).fetchall()
        return [_conversation_from_row(row) for row in rows]

    def _rename_conversation(self, conversation_id: str, name: str) -> Conversation:
        normalized_name = name.strip()
        if not normalized_name:
            raise ValueError("conversation name must not be blank")
        now = _utcnow()
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE conversations SET name = ?, updated_at = ? WHERE id = ?",
                (normalized_name, _serialize_datetime(now), conversation_id),
            )
            if cursor.rowcount != 1:
                raise NotFoundError(f"conversation not found: {conversation_id}")
            row = connection.execute(
                "SELECT * FROM conversations WHERE id = ?", (conversation_id,)
            ).fetchone()
        if row is None:
            raise NotFoundError(f"conversation not found: {conversation_id}")
        return _conversation_from_row(row)

    def _set_conversation_archived(self, conversation_id: str, archived: bool) -> Conversation:
        now = _utcnow()
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE conversations SET archived_at = ?, updated_at = ? WHERE id = ?",
                (
                    _serialize_datetime(now) if archived else None,
                    _serialize_datetime(now),
                    conversation_id,
                ),
            )
            if cursor.rowcount != 1:
                raise NotFoundError(f"conversation not found: {conversation_id}")
            row = connection.execute(
                "SELECT * FROM conversations WHERE id = ?", (conversation_id,)
            ).fetchone()
        if row is None:
            raise NotFoundError(f"conversation not found: {conversation_id}")
        return _conversation_from_row(row)

    def _delete_conversation(self, conversation_id: str) -> None:
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM conversations WHERE id = ?", (conversation_id,)
            )
            if cursor.rowcount != 1:
                raise NotFoundError(f"conversation not found: {conversation_id}")

    def _get_workspace_revision(self, workspace_id: str) -> WorkspaceRevision:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT id, revision, updated_at FROM workspaces WHERE id = ?", (workspace_id,)
            ).fetchone()
        if row is None:
            raise NotFoundError(f"workspace not found: {workspace_id}")
        return _workspace_revision_from_row(row)

    def _advance_workspace_revision(
        self, workspace_id: str, expected_revision: int
    ) -> WorkspaceRevision:
        now = _utcnow()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                UPDATE workspaces
                SET revision = revision + 1, updated_at = ?
                WHERE id = ? AND revision = ?
                """,
                (_serialize_datetime(now), workspace_id, expected_revision),
            )
            if cursor.rowcount != 1:
                exists = connection.execute(
                    "SELECT 1 FROM workspaces WHERE id = ?", (workspace_id,)
                ).fetchone()
                if exists is None:
                    raise NotFoundError(f"workspace not found: {workspace_id}")
                raise WorkspaceRevisionConflictError(
                    f"workspace revision is not {expected_revision}: {workspace_id}"
                )
            row = connection.execute(
                "SELECT id, revision, updated_at FROM workspaces WHERE id = ?", (workspace_id,)
            ).fetchone()
            if row is None:
                raise NotFoundError(f"workspace not found: {workspace_id}")
            connection.commit()
            return _workspace_revision_from_row(row)
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _enqueue_request(self, conversation_id: str, content: str) -> PendingRequest:
        now = _utcnow()
        request_id = str(uuid4())
        with self._connect() as connection:
            _require_conversation(connection, conversation_id)
            cursor = connection.execute(
                """
                INSERT INTO pending_requests(
                    id, conversation_id, content, status, created_at, updated_at
                )
                VALUES (?, ?, ?, 'queued', ?, ?)
                """,
                (
                    request_id,
                    conversation_id,
                    content,
                    _serialize_datetime(now),
                    _serialize_datetime(now),
                ),
            )
            _touch_conversation(connection, conversation_id, now)
            sequence = cursor.lastrowid
        if sequence is None:
            raise ConversationStoreError("SQLite did not assign a queue sequence")
        return PendingRequest(
            id=request_id,
            conversation_id=conversation_id,
            sequence=sequence,
            content=content,
            status=RequestStatus.QUEUED,
            created_at=now,
            updated_at=now,
        )

    def _edit_pending_request(self, request_id: str, content: str) -> PendingRequest:
        now = _utcnow()
        with self._connect() as connection:
            row = _require_request(connection, request_id)
            if row["status"] != RequestStatus.QUEUED:
                raise RequestNotPendingError(f"request is not queued: {request_id}")
            cursor = connection.execute(
                """
                UPDATE pending_requests SET content = ?, updated_at = ?
                WHERE id = ? AND status = 'queued'
                """,
                (content, _serialize_datetime(now), request_id),
            )
            if cursor.rowcount != 1:
                raise RequestNotPendingError(f"request is not queued: {request_id}")
            _touch_conversation(connection, str(row["conversation_id"]), now)
            updated = connection.execute(
                "SELECT * FROM pending_requests WHERE id = ?", (request_id,)
            ).fetchone()
        if updated is None:
            raise NotFoundError(f"request not found: {request_id}")
        return _request_from_row(updated)

    def _cancel_pending_request(self, request_id: str) -> PendingRequest:
        now = _utcnow()
        with self._connect() as connection:
            row = _require_request(connection, request_id)
            if row["status"] != RequestStatus.QUEUED:
                raise RequestNotPendingError(f"request is not queued: {request_id}")
            cursor = connection.execute(
                """
                UPDATE pending_requests SET status = 'canceled', updated_at = ?
                WHERE id = ? AND status = 'queued'
                """,
                (_serialize_datetime(now), request_id),
            )
            if cursor.rowcount != 1:
                raise RequestNotPendingError(f"request is not queued: {request_id}")
            _touch_conversation(connection, str(row["conversation_id"]), now)
            updated = connection.execute(
                "SELECT * FROM pending_requests WHERE id = ?", (request_id,)
            ).fetchone()
        if updated is None:
            raise NotFoundError(f"request not found: {request_id}")
        return _request_from_row(updated)

    def _list_pending_requests(self, conversation_id: str) -> list[PendingRequest]:
        with self._connect() as connection:
            _require_conversation(connection, conversation_id)
            rows = connection.execute(
                """
                SELECT * FROM pending_requests
                WHERE conversation_id = ? AND status = 'queued'
                ORDER BY queue_sequence
                """,
                (conversation_id,),
            ).fetchall()
        return [_request_from_row(row) for row in rows]

    def _get_request(self, request_id: str) -> PendingRequest:
        with self._connect() as connection:
            return _request_from_row(_require_request(connection, request_id))

    def _start_next_turn(self, conversation_id: str, base_seed: int | None) -> Turn | None:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            _require_conversation(connection, conversation_id)
            active = connection.execute(
                "SELECT 1 FROM turns WHERE conversation_id = ? AND status = 'active'",
                (conversation_id,),
            ).fetchone()
            if active is not None:
                raise ActiveTurnExistsError(
                    f"conversation already has an active turn: {conversation_id}"
                )
            request = connection.execute(
                """
                SELECT * FROM pending_requests
                WHERE conversation_id = ? AND status = 'queued'
                ORDER BY queue_sequence
                LIMIT 1
                """,
                (conversation_id,),
            ).fetchone()
            if request is None:
                connection.commit()
                return None
            now = _utcnow()
            selected_seed = secrets.randbits(31) if base_seed is None else base_seed
            turn = Turn(
                id=str(uuid4()),
                conversation_id=conversation_id,
                request_id=str(request["id"]),
                status=TurnStatus.ACTIVE,
                started_at=now,
                base_seed=selected_seed,
            )
            connection.execute(
                """
                INSERT INTO turns(
                    id, conversation_id, request_id, status, started_at, base_seed
                )
                VALUES (?, ?, ?, 'active', ?, ?)
                """,
                (
                    turn.id,
                    turn.conversation_id,
                    turn.request_id,
                    _serialize_datetime(now),
                    turn.base_seed,
                ),
            )
            connection.execute(
                "UPDATE pending_requests SET status = 'dequeued', updated_at = ? WHERE id = ?",
                (_serialize_datetime(now), turn.request_id),
            )
            _touch_conversation(connection, conversation_id, now)
            connection.commit()
            return turn
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _list_turns(self, conversation_id: str) -> list[Turn]:
        with self._connect() as connection:
            _require_conversation(connection, conversation_id)
            rows = connection.execute(
                "SELECT * FROM turns WHERE conversation_id = ? ORDER BY started_at, id",
                (conversation_id,),
            ).fetchall()
        return [_turn_from_row(row) for row in rows]

    def _recover_stale_active_turns(self) -> list[Turn]:
        now = _utcnow()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                "SELECT id FROM turns WHERE status = 'active' ORDER BY started_at, id"
            ).fetchall()
            for row in rows:
                connection.execute(
                    """
                    UPDATE turns
                    SET status = 'finished', ended_at = ?, terminal_outcome_kind = 'abandoned',
                        terminal_reason_code = 'recovered_stale_active_turn'
                    WHERE id = ? AND status = 'active' AND terminal_outcome_kind IS NULL
                    """,
                    (_serialize_datetime(now), str(row["id"])),
                )
            recovered = [_turn_from_row(_require_turn(connection, str(row["id"]))) for row in rows]
            connection.commit()
            return recovered
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _grant(
        self,
        conversation_id: str,
        permission: str,
        scope: str,
        expires_at: datetime | None,
    ) -> Grant:
        if not permission or not scope:
            raise ValueError("permission and scope must not be empty")
        now = _utcnow()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            _require_conversation(connection, conversation_id)
            existing = connection.execute(
                """
                SELECT * FROM grants
                WHERE conversation_id = ? AND permission = ? AND scope = ? AND revoked_at IS NULL
                """,
                (conversation_id, permission, scope),
            ).fetchone()
            if existing is not None:
                existing_expiry = existing["expires_at"]
                if existing_expiry is None or _parse_datetime(str(existing_expiry)) > now:
                    return _grant_from_row(existing)
                connection.execute(
                    "UPDATE grants SET revoked_at = ? WHERE id = ?",
                    (_serialize_datetime(now), str(existing["id"])),
                )
            grant = Grant(
                id=str(uuid4()),
                conversation_id=conversation_id,
                permission=permission,
                scope=scope,
                granted_at=now,
                expires_at=expires_at,
            )
            connection.execute(
                """
                INSERT INTO grants(id, conversation_id, permission, scope, granted_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    grant.id,
                    conversation_id,
                    permission,
                    scope,
                    _serialize_datetime(now),
                    _serialize_datetime(expires_at) if expires_at is not None else None,
                ),
            )
            _touch_conversation(connection, conversation_id, now)
        return grant

    def _get_session_policy(self, conversation_id: str) -> SessionPolicy:
        now = _serialize_datetime(_utcnow())
        with self._connect() as connection:
            _require_conversation(connection, conversation_id)
            rows = connection.execute(
                """
                SELECT * FROM grants
                WHERE conversation_id = ?
                  AND revoked_at IS NULL
                  AND (expires_at IS NULL OR expires_at > ?)
                ORDER BY granted_at, id
                """,
                (conversation_id, now),
            ).fetchall()
        return SessionPolicy(
            conversation_id=conversation_id,
            grants=tuple(_grant_from_row(row) for row in rows),
        )

    def _revoke_grant(self, conversation_id: str, grant_id: str) -> Grant:
        now = _utcnow()
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM grants
                WHERE id = ? AND conversation_id = ? AND revoked_at IS NULL
                """,
                (grant_id, conversation_id),
            ).fetchone()
            if row is None:
                raise NotFoundError(f"active grant not found: {grant_id}")
            connection.execute(
                "UPDATE grants SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL",
                (_serialize_datetime(now), grant_id),
            )
            _touch_conversation(connection, conversation_id, now)
        return _grant_from_row(row)

    def _append_agent_step(
        self,
        turn_id: str,
        seed: int,
        tool_calls: tuple[ToolCall, ...],
        tool_results: tuple[ToolResult, ...],
    ) -> AgentStep:
        now = _utcnow()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            turn = _require_turn(connection, turn_id)
            if turn["status"] != TurnStatus.ACTIVE:
                raise TurnNotActiveError(f"turn is not active: {turn_id}")
            sequence_row = connection.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 FROM agent_steps WHERE turn_id = ?",
                (turn_id,),
            ).fetchone()
            if sequence_row is None:
                raise ConversationStoreError("could not allocate agent step sequence")
            step = AgentStep(
                id=str(uuid4()),
                turn_id=turn_id,
                sequence=int(sequence_row[0]),
                seed=seed,
                tool_calls=tool_calls,
                tool_results=tool_results,
                created_at=now,
            )
            connection.execute(
                """
                INSERT INTO agent_steps(
                    id, turn_id, sequence, seed, tool_calls, tool_results, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    step.id,
                    turn_id,
                    step.sequence,
                    step.seed,
                    _serialize_tool_calls(tool_calls),
                    _serialize_tool_results(tool_results),
                    _serialize_datetime(now),
                ),
            )
            _touch_conversation(connection, str(turn["conversation_id"]), now)
            connection.commit()
            return step
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _list_agent_steps(self, turn_id: str) -> list[AgentStep]:
        with self._connect() as connection:
            _require_turn(connection, turn_id)
            rows = connection.execute(
                "SELECT * FROM agent_steps WHERE turn_id = ? ORDER BY sequence", (turn_id,)
            ).fetchall()
        return [_agent_step_from_row(row) for row in rows]

    def _finish_turn(
        self,
        turn_id: str,
        kind: TerminalOutcomeKind,
        reason_code: str,
        detail: str | None,
    ) -> Turn:
        if not reason_code:
            raise ValueError("terminal outcome reason_code must not be empty")
        now = _utcnow()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = _require_turn(connection, turn_id)
            if row["terminal_outcome_kind"] is not None:
                raise TerminalOutcomeAlreadySetError(
                    f"terminal outcome already set for turn: {turn_id}"
                )
            if row["status"] != TurnStatus.ACTIVE:
                raise TurnNotActiveError(f"turn is not active: {turn_id}")
            connection.execute(
                """
                UPDATE turns
                SET status = 'finished', ended_at = ?, terminal_outcome_kind = ?,
                    terminal_reason_code = ?, terminal_detail = ?
                WHERE id = ? AND terminal_outcome_kind IS NULL
                """,
                (_serialize_datetime(now), kind.value, reason_code, detail, turn_id),
            )
            _touch_conversation(connection, str(row["conversation_id"]), now)
            updated = _require_turn(connection, turn_id)
            connection.commit()
            return _turn_from_row(updated)
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _append_canonical_history(
        self,
        turn_id: str,
        kind: CanonicalHistoryEntryKind,
        payload: Mapping[str, JsonValue],
    ) -> CanonicalHistoryEntry:
        _reject_reasoning(payload)
        payload_json = _dump_json(payload)
        now = _utcnow()
        entry_id = str(uuid4())
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            turn = _require_turn(connection, turn_id)
            if turn["status"] != TurnStatus.ACTIVE:
                raise TurnNotActiveError(f"turn is not active: {turn_id}")
            conversation_id = str(turn["conversation_id"])
            cursor = connection.execute(
                """
                INSERT INTO canonical_history(
                    id, conversation_id, turn_id, entry_type, payload, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    entry_id,
                    conversation_id,
                    turn_id,
                    kind.value,
                    payload_json,
                    _serialize_datetime(now),
                ),
            )
            sequence = cursor.lastrowid
            if sequence is None:
                raise ConversationStoreError("SQLite did not assign a history sequence")
            _touch_conversation(connection, conversation_id, now)
            connection.commit()
            return CanonicalHistoryEntry(
                id=entry_id,
                sequence=sequence,
                conversation_id=conversation_id,
                turn_id=turn_id,
                kind=kind,
                payload=_load_json_object(payload_json),
                created_at=now,
            )
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _list_canonical_history(
        self, conversation_id: str, turn_id: str | None
    ) -> list[CanonicalHistoryEntry]:
        with self._connect() as connection:
            _require_conversation(connection, conversation_id)
            if turn_id is None:
                rows = connection.execute(
                    """
                    SELECT * FROM canonical_history
                    WHERE conversation_id = ?
                    ORDER BY sequence
                    """,
                    (conversation_id,),
                ).fetchall()
            else:
                turn = _require_turn(connection, turn_id)
                if turn["conversation_id"] != conversation_id:
                    raise ValueError("history turn does not belong to conversation")
                rows = connection.execute(
                    """
                    SELECT * FROM canonical_history
                    WHERE conversation_id = ? AND turn_id = ?
                    ORDER BY sequence
                    """,
                    (conversation_id, turn_id),
                ).fetchall()
        return [_canonical_history_entry_from_row(row) for row in rows]

    def _add_feedback(
        self,
        conversation_id: str,
        rating: int,
        comment: str | None,
        turn_id: str | None,
    ) -> Feedback:
        if rating < -1 or rating > 1:
            raise ValueError("feedback rating must be between -1 and 1")
        now = _utcnow()
        feedback = Feedback(
            id=str(uuid4()),
            conversation_id=conversation_id,
            rating=rating,
            comment=comment,
            created_at=now,
            turn_id=turn_id,
        )
        with self._connect() as connection:
            _require_conversation(connection, conversation_id)
            if turn_id is not None:
                turn = _require_turn(connection, turn_id)
                if turn["conversation_id"] != conversation_id:
                    raise ValueError("feedback turn does not belong to conversation")
            connection.execute(
                """
                INSERT INTO feedback(id, conversation_id, turn_id, rating, comment, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    feedback.id,
                    conversation_id,
                    turn_id,
                    rating,
                    comment,
                    _serialize_datetime(now),
                ),
            )
            _touch_conversation(connection, conversation_id, now)
        return feedback

    def _list_feedback(self, conversation_id: str) -> list[Feedback]:
        with self._connect() as connection:
            _require_conversation(connection, conversation_id)
            rows = connection.execute(
                "SELECT * FROM feedback WHERE conversation_id = ? ORDER BY created_at, id",
                (conversation_id,),
            ).fetchall()
        return [_feedback_from_row(row) for row in rows]

    def _append_outbox_event(
        self,
        conversation_id: str | None,
        event_type: str,
        payload: Mapping[str, JsonValue],
        idempotency_key: str,
    ) -> DomainEvent:
        if not event_type or not idempotency_key:
            raise ValueError("event_type and idempotency_key must not be empty")
        payload_json = _dump_json(payload)
        now = _utcnow()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM outbox WHERE idempotency_key = ?", (idempotency_key,)
            ).fetchone()
            if existing is not None:
                same_event = (
                    existing["conversation_id"] == conversation_id
                    and existing["event_type"] == event_type
                    and existing["payload"] == payload_json
                )
                if not same_event:
                    raise IdempotencyConflictError(
                        f"idempotency key belongs to a different event: {idempotency_key}"
                    )
                return _domain_event_from_row(existing)
            if conversation_id is not None:
                _require_conversation(connection, conversation_id)
            event_id = str(uuid4())
            cursor = connection.execute(
                """
                INSERT INTO outbox(
                    id, conversation_id, event_type, payload, idempotency_key, occurred_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    conversation_id,
                    event_type,
                    payload_json,
                    idempotency_key,
                    _serialize_datetime(now),
                ),
            )
            if conversation_id is not None:
                _touch_conversation(connection, conversation_id, now)
            sequence = cursor.lastrowid
        if sequence is None:
            raise ConversationStoreError("SQLite did not assign an outbox sequence")
        return DomainEvent(
            id=event_id,
            sequence=sequence,
            event_type=event_type,
            payload=_load_json_object(payload_json),
            idempotency_key=idempotency_key,
            occurred_at=now,
            conversation_id=conversation_id,
        )

    def _list_outbox_events(
        self, after_sequence: int, limit: int, unpublished_only: bool
    ) -> list[DomainEvent]:
        if after_sequence < 0 or limit < 1:
            raise ValueError("after_sequence must be non-negative and limit must be positive")
        query = (
            """
            SELECT * FROM outbox
            WHERE sequence > ? AND published_at IS NULL
            ORDER BY sequence
            LIMIT ?
            """
            if unpublished_only
            else """
            SELECT * FROM outbox
            WHERE sequence > ?
            ORDER BY sequence
            LIMIT ?
            """
        )
        with self._connect() as connection:
            rows = connection.execute(query, (after_sequence, limit)).fetchall()
        return [_domain_event_from_row(row) for row in rows]

    def _mark_outbox_published(self, event_id: str) -> DomainEvent:
        now = _utcnow()
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM outbox WHERE id = ?", (event_id,)).fetchone()
            if row is None:
                raise NotFoundError(f"outbox event not found: {event_id}")
            if row["published_at"] is None:
                connection.execute(
                    "UPDATE outbox SET published_at = ? WHERE id = ? AND published_at IS NULL",
                    (_serialize_datetime(now), event_id),
                )
                row = connection.execute(
                    "SELECT * FROM outbox WHERE id = ?", (event_id,)
                ).fetchone()
        if row is None:
            raise NotFoundError(f"outbox event not found: {event_id}")
        return _domain_event_from_row(row)

    def _purge_inactive(self, before: datetime) -> list[str]:
        serialized = _serialize_datetime(before)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                """
                SELECT id FROM conversations
                WHERE last_active_at < ?
                ORDER BY last_active_at, id
                """,
                (serialized,),
            ).fetchall()
            conversation_ids = [str(row["id"]) for row in rows]
            connection.executemany(
                "DELETE FROM conversations WHERE id = ?",
                ((conversation_id,) for conversation_id in conversation_ids),
            )
            connection.commit()
            return conversation_ids
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _serialize_datetime(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("datetime must be timezone-aware")
    return value.astimezone(UTC).isoformat()


def _parse_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _workspace_revision_from_row(row: sqlite3.Row) -> WorkspaceRevision:
    return WorkspaceRevision(
        workspace_id=str(row["id"]),
        revision=int(row["revision"]),
        updated_at=_parse_datetime(str(row["updated_at"])),
    )


def _conversation_from_row(row: sqlite3.Row) -> Conversation:
    archived_at = row["archived_at"]
    return Conversation(
        id=str(row["id"]),
        workspace_id=str(row["workspace_id"]),
        created_at=_parse_datetime(str(row["created_at"])),
        updated_at=_parse_datetime(str(row["updated_at"])),
        last_active_at=_parse_datetime(str(row["last_active_at"])),
        name=str(row["name"]),
        archived_at=(_parse_datetime(str(archived_at)) if archived_at is not None else None),
    )


def _request_from_row(row: sqlite3.Row) -> PendingRequest:
    return PendingRequest(
        id=str(row["id"]),
        conversation_id=str(row["conversation_id"]),
        sequence=int(row["queue_sequence"]),
        content=str(row["content"]),
        status=RequestStatus(str(row["status"])),
        created_at=_parse_datetime(str(row["created_at"])),
        updated_at=_parse_datetime(str(row["updated_at"])),
    )


def _grant_from_row(row: sqlite3.Row) -> Grant:
    expires_at = row["expires_at"]
    return Grant(
        id=str(row["id"]),
        conversation_id=str(row["conversation_id"]),
        permission=str(row["permission"]),
        scope=str(row["scope"]),
        granted_at=_parse_datetime(str(row["granted_at"])),
        expires_at=_parse_datetime(str(expires_at)) if expires_at is not None else None,
    )


def _feedback_from_row(row: sqlite3.Row) -> Feedback:
    turn_id = row["turn_id"]
    return Feedback(
        id=str(row["id"]),
        conversation_id=str(row["conversation_id"]),
        rating=int(row["rating"]),
        comment=str(row["comment"]) if row["comment"] is not None else None,
        created_at=_parse_datetime(str(row["created_at"])),
        turn_id=str(turn_id) if turn_id is not None else None,
    )


def _domain_event_from_row(row: sqlite3.Row) -> DomainEvent:
    conversation_id = row["conversation_id"]
    published_at = row["published_at"]
    return DomainEvent(
        id=str(row["id"]),
        sequence=int(row["sequence"]),
        event_type=str(row["event_type"]),
        payload=_load_json_object(str(row["payload"])),
        idempotency_key=str(row["idempotency_key"]),
        occurred_at=_parse_datetime(str(row["occurred_at"])),
        conversation_id=str(conversation_id) if conversation_id is not None else None,
        published_at=_parse_datetime(str(published_at)) if published_at is not None else None,
    )


def _turn_from_row(row: sqlite3.Row) -> Turn:
    ended_at = row["ended_at"]
    terminal_outcome_kind = row["terminal_outcome_kind"]
    terminal_outcome = None
    if terminal_outcome_kind is not None:
        if ended_at is None:
            raise ConversationStoreError("terminal turn is missing ended_at")
        reason_code = row["terminal_reason_code"]
        if reason_code is None:
            raise ConversationStoreError("terminal turn is missing reason_code")
        terminal_outcome = TerminalOutcome(
            kind=TerminalOutcomeKind(str(terminal_outcome_kind)),
            reason_code=str(reason_code),
            recorded_at=_parse_datetime(str(ended_at)),
            detail=str(row["terminal_detail"]) if row["terminal_detail"] is not None else None,
        )
    return Turn(
        id=str(row["id"]),
        conversation_id=str(row["conversation_id"]),
        request_id=str(row["request_id"]),
        status=TurnStatus(str(row["status"])),
        started_at=_parse_datetime(str(row["started_at"])),
        base_seed=int(row["base_seed"]),
        ended_at=_parse_datetime(str(ended_at)) if ended_at is not None else None,
        terminal_outcome=terminal_outcome,
    )


def _agent_step_from_row(row: sqlite3.Row) -> AgentStep:
    return AgentStep(
        id=str(row["id"]),
        turn_id=str(row["turn_id"]),
        sequence=int(row["sequence"]),
        seed=int(row["seed"]),
        tool_calls=_deserialize_tool_calls(str(row["tool_calls"])),
        tool_results=_deserialize_tool_results(str(row["tool_results"])),
        created_at=_parse_datetime(str(row["created_at"])),
    )


def _canonical_history_entry_from_row(row: sqlite3.Row) -> CanonicalHistoryEntry:
    return CanonicalHistoryEntry(
        id=str(row["id"]),
        sequence=int(row["sequence"]),
        conversation_id=str(row["conversation_id"]),
        turn_id=str(row["turn_id"]),
        kind=CanonicalHistoryEntryKind(str(row["entry_type"])),
        payload=_load_json_object(str(row["payload"])),
        created_at=_parse_datetime(str(row["created_at"])),
    )


def _serialize_tool_calls(tool_calls: Sequence[ToolCall]) -> str:
    payload: list[dict[str, JsonValue]] = []
    for call in tool_calls:
        payload.append(
            {
                "id": call.id,
                "name": call.name,
                "arguments": call.arguments,
                "idempotency_key": call.idempotency_key,
            }
        )
    return _dump_json(payload)


def _serialize_tool_results(tool_results: Sequence[ToolResult]) -> str:
    payload: list[dict[str, JsonValue]] = []
    for result in tool_results:
        payload.append(
            {
                "tool_call_id": result.tool_call_id,
                "status": result.status.value,
                "retryable": result.retryable,
                "data": result.data,
                "error": result.error,
                "meta": result.meta,
            }
        )
    return _dump_json(payload)


def _deserialize_tool_calls(value: str) -> tuple[ToolCall, ...]:
    raw = _load_json_array(value)
    return tuple(
        ToolCall(
            id=_string_field(item, "id"),
            name=_string_field(item, "name"),
            arguments=_mapping_field(item, "arguments"),
            idempotency_key=_optional_string_field(item, "idempotency_key"),
        )
        for item in raw
    )


def _deserialize_tool_results(value: str) -> tuple[ToolResult, ...]:
    raw = _load_json_array(value)
    return tuple(
        ToolResult(
            tool_call_id=_string_field(item, "tool_call_id"),
            status=ToolResultStatus(_string_field(item, "status")),
            retryable=_bool_field(item, "retryable"),
            data=item.get("data"),
            error=_optional_mapping_field(item, "error"),
            meta=_mapping_field(item, "meta"),
        )
        for item in raw
    )


def _dump_json(value: JsonValue) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def _load_json_array(value: str) -> list[Mapping[str, JsonValue]]:
    decoded = cast(object, json.loads(value))
    if not isinstance(decoded, list):
        raise ConversationStoreError("invalid stored agent step")
    result: list[Mapping[str, JsonValue]] = []
    for item in cast(list[object], decoded):
        if not isinstance(item, dict):
            raise ConversationStoreError("invalid stored agent step")
        result.append(cast(dict[str, JsonValue], item))
    return result


def _load_json_object(value: str) -> Mapping[str, JsonValue]:
    decoded = cast(object, json.loads(value))
    if not isinstance(decoded, dict):
        raise ConversationStoreError("invalid stored event payload")
    return cast(dict[str, JsonValue], decoded)


def _string_field(item: Mapping[str, JsonValue], key: str) -> str:
    value = item.get(key)
    if not isinstance(value, str):
        raise ConversationStoreError(f"invalid stored string field: {key}")
    return value


def _optional_string_field(item: Mapping[str, JsonValue], key: str) -> str | None:
    value = item.get(key)
    if value is not None and not isinstance(value, str):
        raise ConversationStoreError(f"invalid stored optional string field: {key}")
    return value


def _bool_field(item: Mapping[str, JsonValue], key: str) -> bool:
    value = item.get(key)
    if not isinstance(value, bool):
        raise ConversationStoreError(f"invalid stored boolean field: {key}")
    return value


def _mapping_field(item: Mapping[str, JsonValue], key: str) -> Mapping[str, JsonValue]:
    value = item.get(key)
    if not isinstance(value, dict):
        raise ConversationStoreError(f"invalid stored object field: {key}")
    return value


def _optional_mapping_field(
    item: Mapping[str, JsonValue], key: str
) -> Mapping[str, JsonValue] | None:
    value = item.get(key)
    if value is not None and not isinstance(value, dict):
        raise ConversationStoreError(f"invalid stored optional object field: {key}")
    return value


def _reject_reasoning(value: JsonValue, path: tuple[str, ...] = ()) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = key.lower().replace("-", "_")
            if normalized in {"reasoning", "thinking"} or normalized.endswith(
                ("_reasoning", "_thinking")
            ):
                location = ".".join((*path, key))
                raise ValueError(f"canonical history cannot persist reasoning: {location}")
            _reject_reasoning(child, (*path, key))
    elif isinstance(value, Sequence) and not isinstance(value, str):
        for index, child in enumerate(value):
            _reject_reasoning(child, (*path, str(index)))


def _require_conversation(connection: sqlite3.Connection, conversation_id: str) -> None:
    row = connection.execute(
        "SELECT 1 FROM conversations WHERE id = ?", (conversation_id,)
    ).fetchone()
    if row is None:
        raise NotFoundError(f"conversation not found: {conversation_id}")


def _require_request(connection: sqlite3.Connection, request_id: str) -> sqlite3.Row:
    row = connection.execute(
        "SELECT * FROM pending_requests WHERE id = ?", (request_id,)
    ).fetchone()
    if row is None:
        raise NotFoundError(f"request not found: {request_id}")
    return row


def _require_turn(connection: sqlite3.Connection, turn_id: str) -> sqlite3.Row:
    row = connection.execute("SELECT * FROM turns WHERE id = ?", (turn_id,)).fetchone()
    if row is None:
        raise NotFoundError(f"turn not found: {turn_id}")
    return row


def _touch_conversation(
    connection: sqlite3.Connection, conversation_id: str, now: datetime
) -> None:
    serialized = _serialize_datetime(now)
    connection.execute(
        "UPDATE conversations SET updated_at = ?, last_active_at = ? WHERE id = ?",
        (serialized, serialized, conversation_id),
    )
