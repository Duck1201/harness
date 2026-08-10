import asyncio
import json
import re
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from uuid import uuid4

from ..domain import JsonValue, TerminalOutcomeKind
from .models import EvalPhase, EvalRunStatus, EvalTier, TaskVerdict


class EvalStoreError(Exception):
    pass


class EvalNotFoundError(EvalStoreError):
    pass


@dataclass(frozen=True, slots=True)
class EvalRun:
    id: str
    experiment_id: str
    tier: EvalTier
    phase: EvalPhase
    status: EvalRunStatus
    seeds: tuple[int, ...]
    created_at: datetime
    updated_at: datetime
    reason_code: str | None = None


@dataclass(frozen=True, slots=True)
class EvalArm:
    id: str
    run_id: str
    arm_id: str
    settings: Mapping[str, JsonValue]
    created_at: datetime


@dataclass(frozen=True, slots=True)
class EvalCase:
    id: str
    run_id: str
    arm_id: str
    fixture_id: str
    seed: int
    order_index: int
    verdict: TaskVerdict
    terminal_outcome_kind: TerminalOutcomeKind | None
    terminal_outcome_reason: str | None
    security_violations: int
    created_at: datetime


@dataclass(frozen=True, slots=True)
class EvalMetric:
    id: str
    run_id: str
    arm_id: str
    case_id: str | None
    name: str
    value: float
    created_at: datetime


@dataclass(frozen=True, slots=True)
class EvalReport:
    id: str
    run_id: str
    payload: Mapping[str, JsonValue]
    created_at: datetime


@dataclass(frozen=True, slots=True)
class RegressionDraft:
    id: str
    source_feedback_sha256: str
    source_turn_sha256: str
    rating: int
    comment_present: bool
    terminal_outcome_kind: TerminalOutcomeKind
    terminal_outcome_reason: str
    created_at: datetime


class EvalStore:
    def __init__(self, database: str | Path) -> None:
        self._database = str(database)

    @property
    def database(self) -> str:
        return self._database

    async def initialize(self) -> None:
        await asyncio.to_thread(self._initialize)

    async def create_run(
        self,
        *,
        experiment_id: str,
        tier: EvalTier,
        phase: EvalPhase,
        seeds: tuple[int, ...],
    ) -> EvalRun:
        return await asyncio.to_thread(self._create_run, experiment_id, tier, phase, seeds)

    async def get_run(self, run_id: str) -> EvalRun:
        return await asyncio.to_thread(self._get_run, run_id)

    async def list_runs(self) -> list[EvalRun]:
        return await asyncio.to_thread(self._list_runs)

    async def set_run_status(
        self,
        run_id: str,
        status: EvalRunStatus,
        *,
        reason_code: str | None = None,
    ) -> EvalRun:
        return await asyncio.to_thread(self._set_run_status, run_id, status, reason_code)

    async def create_arm(
        self,
        run_id: str,
        arm_id: str,
        *,
        settings: Mapping[str, JsonValue],
    ) -> EvalArm:
        return await asyncio.to_thread(self._create_arm, run_id, arm_id, settings)

    async def list_arms(self, run_id: str) -> list[EvalArm]:
        return await asyncio.to_thread(self._list_arms, run_id)

    async def record_case(
        self,
        *,
        run_id: str,
        arm_id: str,
        fixture_id: str,
        seed: int,
        order_index: int,
        verdict: TaskVerdict,
        terminal_outcome_kind: TerminalOutcomeKind | None,
        terminal_outcome_reason: str | None,
        security_violations: int,
    ) -> EvalCase:
        return await asyncio.to_thread(
            self._record_case,
            run_id,
            arm_id,
            fixture_id,
            seed,
            order_index,
            verdict,
            terminal_outcome_kind,
            terminal_outcome_reason,
            security_violations,
        )

    async def list_cases(self, run_id: str) -> list[EvalCase]:
        return await asyncio.to_thread(self._list_cases, run_id)

    async def record_metric(
        self,
        *,
        run_id: str,
        arm_id: str,
        case_id: str | None,
        name: str,
        value: float,
    ) -> EvalMetric:
        return await asyncio.to_thread(self._record_metric, run_id, arm_id, case_id, name, value)

    async def list_metrics(self, run_id: str) -> list[EvalMetric]:
        return await asyncio.to_thread(self._list_metrics, run_id)

    async def save_report(self, run_id: str, payload: Mapping[str, JsonValue]) -> EvalReport:
        return await asyncio.to_thread(self._save_report, run_id, payload)

    async def get_report(self, run_id: str) -> EvalReport:
        return await asyncio.to_thread(self._get_report, run_id)

    async def list_reports(self) -> list[EvalReport]:
        return await asyncio.to_thread(self._list_reports)

    async def create_regression_draft(
        self,
        *,
        source_feedback_sha256: str,
        source_turn_sha256: str,
        rating: int,
        comment_present: bool,
        terminal_outcome_kind: TerminalOutcomeKind,
        terminal_outcome_reason: str,
    ) -> RegressionDraft:
        return await asyncio.to_thread(
            self._create_regression_draft,
            source_feedback_sha256,
            source_turn_sha256,
            rating,
            comment_present,
            terminal_outcome_kind,
            terminal_outcome_reason,
        )

    async def list_regression_drafts(self) -> list[RegressionDraft]:
        return await asyncio.to_thread(self._list_regression_drafts)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS eval_runs (
                    id TEXT PRIMARY KEY,
                    experiment_id TEXT NOT NULL,
                    tier TEXT NOT NULL,
                    phase TEXT NOT NULL,
                    status TEXT NOT NULL,
                    seeds TEXT NOT NULL,
                    reason_code TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS eval_arms (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES eval_runs(id) ON DELETE CASCADE,
                    arm_id TEXT NOT NULL,
                    settings TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(run_id, arm_id)
                );
                CREATE TABLE IF NOT EXISTS eval_cases (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES eval_runs(id) ON DELETE CASCADE,
                    arm_id TEXT NOT NULL REFERENCES eval_arms(id) ON DELETE CASCADE,
                    fixture_id TEXT NOT NULL,
                    seed INTEGER NOT NULL,
                    order_index INTEGER NOT NULL CHECK(order_index >= 0),
                    verdict TEXT NOT NULL,
                    terminal_outcome_kind TEXT,
                    terminal_outcome_reason TEXT,
                    security_violations INTEGER NOT NULL CHECK(security_violations >= 0),
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS eval_metrics (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES eval_runs(id) ON DELETE CASCADE,
                    arm_id TEXT NOT NULL REFERENCES eval_arms(id) ON DELETE CASCADE,
                    case_id TEXT REFERENCES eval_cases(id) ON DELETE CASCADE,
                    name TEXT NOT NULL,
                    value REAL NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS eval_reports (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL UNIQUE REFERENCES eval_runs(id) ON DELETE CASCADE,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS regression_drafts (
                    id TEXT PRIMARY KEY,
                    source_feedback_sha256 TEXT NOT NULL,
                    source_turn_sha256 TEXT NOT NULL,
                    rating INTEGER NOT NULL CHECK(rating BETWEEN -1 AND 1),
                    comment_present INTEGER NOT NULL CHECK(comment_present IN (0, 1)),
                    terminal_outcome_kind TEXT NOT NULL,
                    terminal_outcome_reason TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )

    def _create_run(
        self,
        experiment_id: str,
        tier: EvalTier,
        phase: EvalPhase,
        seeds: tuple[int, ...],
    ) -> EvalRun:
        if not experiment_id or not seeds or len(set(seeds)) != len(seeds):
            raise ValueError("experiment_id and unique seeds are required")
        if any(seed < 0 or seed > 2**63 - 1 for seed in seeds):
            raise ValueError("seeds must be non-negative signed 64-bit integers")
        now = _utcnow()
        run = EvalRun(
            id=str(uuid4()),
            experiment_id=experiment_id,
            tier=tier,
            phase=phase,
            status=EvalRunStatus.CREATED,
            seeds=seeds,
            created_at=now,
            updated_at=now,
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO eval_runs(
                    id, experiment_id, tier, phase, status, seeds, reason_code,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run.id,
                    run.experiment_id,
                    run.tier.value,
                    run.phase.value,
                    run.status.value,
                    _json(run.seeds),
                    None,
                    _datetime(now),
                    _datetime(now),
                ),
            )
        return run

    def _get_run(self, run_id: str) -> EvalRun:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM eval_runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            raise EvalNotFoundError(f"eval run not found: {run_id}")
        return _run_from_row(row)

    def _list_runs(self) -> list[EvalRun]:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM eval_runs ORDER BY created_at, id").fetchall()
        return [_run_from_row(row) for row in rows]

    def _set_run_status(
        self, run_id: str, status: EvalRunStatus, reason_code: str | None
    ) -> EvalRun:
        now = _utcnow()
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE eval_runs SET status = ?, reason_code = ?, updated_at = ? WHERE id = ?",
                (status.value, reason_code, _datetime(now), run_id),
            )
            if cursor.rowcount != 1:
                raise EvalNotFoundError(f"eval run not found: {run_id}")
        return self._get_run(run_id)

    def _create_arm(self, run_id: str, arm_id: str, settings: Mapping[str, JsonValue]) -> EvalArm:
        if not arm_id:
            raise ValueError("arm_id must not be empty")
        now = _utcnow()
        arm = EvalArm(
            id=str(uuid4()),
            run_id=run_id,
            arm_id=arm_id,
            settings=dict(settings),
            created_at=now,
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO eval_arms(id, run_id, arm_id, settings, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (arm.id, run_id, arm_id, _json(settings), _datetime(now)),
            )
        return arm

    def _list_arms(self, run_id: str) -> list[EvalArm]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM eval_arms WHERE run_id = ? ORDER BY created_at, id",
                (run_id,),
            ).fetchall()
        return [_arm_from_row(row) for row in rows]

    def _record_case(
        self,
        run_id: str,
        arm_id: str,
        fixture_id: str,
        seed: int,
        order_index: int,
        verdict: TaskVerdict,
        terminal_outcome_kind: TerminalOutcomeKind | None,
        terminal_outcome_reason: str | None,
        security_violations: int,
    ) -> EvalCase:
        if not fixture_id or seed < 0 or order_index < 0 or security_violations < 0:
            raise ValueError("invalid eval case metadata")
        now = _utcnow()
        case = EvalCase(
            id=str(uuid4()),
            run_id=run_id,
            arm_id=arm_id,
            fixture_id=fixture_id,
            seed=seed,
            order_index=order_index,
            verdict=verdict,
            terminal_outcome_kind=terminal_outcome_kind,
            terminal_outcome_reason=terminal_outcome_reason,
            security_violations=security_violations,
            created_at=now,
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO eval_cases(
                    id, run_id, arm_id, fixture_id, seed, order_index, verdict,
                    terminal_outcome_kind, terminal_outcome_reason,
                    security_violations, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    case.id,
                    run_id,
                    arm_id,
                    fixture_id,
                    seed,
                    order_index,
                    verdict.value,
                    terminal_outcome_kind.value if terminal_outcome_kind else None,
                    terminal_outcome_reason,
                    security_violations,
                    _datetime(now),
                ),
            )
        return case

    def _list_cases(self, run_id: str) -> list[EvalCase]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM eval_cases WHERE run_id = ? ORDER BY order_index, created_at, id",
                (run_id,),
            ).fetchall()
        return [_case_from_row(row) for row in rows]

    def _record_metric(
        self,
        run_id: str,
        arm_id: str,
        case_id: str | None,
        name: str,
        value: float,
    ) -> EvalMetric:
        if not name:
            raise ValueError("metric name must not be empty")
        now = _utcnow()
        metric = EvalMetric(
            id=str(uuid4()),
            run_id=run_id,
            arm_id=arm_id,
            case_id=case_id,
            name=name,
            value=value,
            created_at=now,
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO eval_metrics(id, run_id, arm_id, case_id, name, value, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (metric.id, run_id, arm_id, case_id, name, value, _datetime(now)),
            )
        return metric

    def _list_metrics(self, run_id: str) -> list[EvalMetric]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM eval_metrics WHERE run_id = ? ORDER BY created_at, id",
                (run_id,),
            ).fetchall()
        return [_metric_from_row(row) for row in rows]

    def _save_report(self, run_id: str, payload: Mapping[str, JsonValue]) -> EvalReport:
        now = _utcnow()
        report = EvalReport(id=str(uuid4()), run_id=run_id, payload=dict(payload), created_at=now)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO eval_reports(id, run_id, payload, created_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    id = excluded.id, payload = excluded.payload, created_at = excluded.created_at
                """,
                (report.id, run_id, _json(payload), _datetime(now)),
            )
        return report

    def _get_report(self, run_id: str) -> EvalReport:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM eval_reports WHERE run_id = ?", (run_id,)
            ).fetchone()
        if row is None:
            raise EvalNotFoundError(f"eval report not found for run: {run_id}")
        return _report_from_row(row)

    def _list_reports(self) -> list[EvalReport]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM eval_reports ORDER BY created_at, id"
            ).fetchall()
        return [_report_from_row(row) for row in rows]

    def _create_regression_draft(
        self,
        source_feedback_sha256: str,
        source_turn_sha256: str,
        rating: int,
        comment_present: bool,
        terminal_outcome_kind: TerminalOutcomeKind,
        terminal_outcome_reason: str,
    ) -> RegressionDraft:
        if (
            not re.fullmatch(r"[0-9a-f]{64}", source_feedback_sha256)
            or not re.fullmatch(r"[0-9a-f]{64}", source_turn_sha256)
            or rating not in {-1, 0, 1}
            or not terminal_outcome_reason
        ):
            raise ValueError("invalid regression draft metadata")
        now = _utcnow()
        draft = RegressionDraft(
            id=str(uuid4()),
            source_feedback_sha256=source_feedback_sha256,
            source_turn_sha256=source_turn_sha256,
            rating=rating,
            comment_present=comment_present,
            terminal_outcome_kind=terminal_outcome_kind,
            terminal_outcome_reason=terminal_outcome_reason,
            created_at=now,
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO regression_drafts(
                    id, source_feedback_sha256, source_turn_sha256, rating,
                    comment_present, terminal_outcome_kind,
                    terminal_outcome_reason, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    draft.id,
                    draft.source_feedback_sha256,
                    draft.source_turn_sha256,
                    draft.rating,
                    int(draft.comment_present),
                    draft.terminal_outcome_kind.value,
                    draft.terminal_outcome_reason,
                    _datetime(now),
                ),
            )
        return draft

    def _list_regression_drafts(self) -> list[RegressionDraft]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM regression_drafts ORDER BY created_at, id"
            ).fetchall()
        return [_draft_from_row(row) for row in rows]


def _run_from_row(row: sqlite3.Row) -> EvalRun:
    raw_seeds = cast(object, json.loads(str(row["seeds"])))
    if not isinstance(raw_seeds, list):
        raise EvalStoreError("stored eval seeds are invalid")
    seed_items = cast(list[object], raw_seeds)
    if not all(isinstance(item, int) for item in seed_items):
        raise EvalStoreError("stored eval seeds are invalid")
    return EvalRun(
        id=str(row["id"]),
        experiment_id=str(row["experiment_id"]),
        tier=EvalTier(str(row["tier"])),
        phase=EvalPhase(str(row["phase"])),
        status=EvalRunStatus(str(row["status"])),
        seeds=tuple(cast(list[int], seed_items)),
        reason_code=str(row["reason_code"]) if row["reason_code"] is not None else None,
        created_at=datetime.fromisoformat(str(row["created_at"])),
        updated_at=datetime.fromisoformat(str(row["updated_at"])),
    )


def _arm_from_row(row: sqlite3.Row) -> EvalArm:
    return EvalArm(
        id=str(row["id"]),
        run_id=str(row["run_id"]),
        arm_id=str(row["arm_id"]),
        settings=_mapping(str(row["settings"])),
        created_at=datetime.fromisoformat(str(row["created_at"])),
    )


def _case_from_row(row: sqlite3.Row) -> EvalCase:
    raw_kind = row["terminal_outcome_kind"]
    return EvalCase(
        id=str(row["id"]),
        run_id=str(row["run_id"]),
        arm_id=str(row["arm_id"]),
        fixture_id=str(row["fixture_id"]),
        seed=int(row["seed"]),
        order_index=int(row["order_index"]),
        verdict=TaskVerdict(str(row["verdict"])),
        terminal_outcome_kind=(
            TerminalOutcomeKind(str(raw_kind)) if raw_kind is not None else None
        ),
        terminal_outcome_reason=(
            str(row["terminal_outcome_reason"])
            if row["terminal_outcome_reason"] is not None
            else None
        ),
        security_violations=int(row["security_violations"]),
        created_at=datetime.fromisoformat(str(row["created_at"])),
    )


def _metric_from_row(row: sqlite3.Row) -> EvalMetric:
    return EvalMetric(
        id=str(row["id"]),
        run_id=str(row["run_id"]),
        arm_id=str(row["arm_id"]),
        case_id=str(row["case_id"]) if row["case_id"] is not None else None,
        name=str(row["name"]),
        value=float(row["value"]),
        created_at=datetime.fromisoformat(str(row["created_at"])),
    )


def _report_from_row(row: sqlite3.Row) -> EvalReport:
    return EvalReport(
        id=str(row["id"]),
        run_id=str(row["run_id"]),
        payload=_mapping(str(row["payload"])),
        created_at=datetime.fromisoformat(str(row["created_at"])),
    )


def _draft_from_row(row: sqlite3.Row) -> RegressionDraft:
    return RegressionDraft(
        id=str(row["id"]),
        source_feedback_sha256=str(row["source_feedback_sha256"]),
        source_turn_sha256=str(row["source_turn_sha256"]),
        rating=int(row["rating"]),
        comment_present=bool(row["comment_present"]),
        terminal_outcome_kind=TerminalOutcomeKind(str(row["terminal_outcome_kind"])),
        terminal_outcome_reason=str(row["terminal_outcome_reason"]),
        created_at=datetime.fromisoformat(str(row["created_at"])),
    )


def _mapping(value: str) -> Mapping[str, JsonValue]:
    decoded = cast(object, json.loads(value))
    if not isinstance(decoded, dict):
        raise EvalStoreError("stored eval JSON is not an object")
    return cast(dict[str, JsonValue], decoded)


def _json(value: JsonValue) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _datetime(value: datetime) -> str:
    return value.isoformat()
