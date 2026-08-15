import asyncio
import hashlib
import random
from collections.abc import Mapping, Sequence
from typing import cast

from ..domain import Feedback, JsonValue, Turn
from .lease import BenchmarkLease, BenchmarkLeaseCanceled
from .models import (
    EvalCatalog,
    EvalPhase,
    EvalRunStatus,
    EvalTier,
    ExperimentDefinition,
    RegressionFixture,
    TaskVerdict,
)
from .oracles import evaluate_oracle
from .runner import CaseRunner, EvalCaseSpec
from .statistics import evaluate_promotion_gate, wilson_interval
from .store import EvalCase, EvalReport, EvalRun, EvalStore, RegressionDraft


class EvalServiceError(Exception):
    pass


class EvalService:
    DEFAULT_SEEDS = (104729, 130363, 155921)

    def __init__(
        self,
        *,
        store: EvalStore,
        catalog: EvalCatalog,
        lease: BenchmarkLease,
        runners: Mapping[EvalTier, CaseRunner] | None = None,
    ) -> None:
        self.store = store
        self.catalog = catalog
        self.lease = lease
        self._runners = dict(runners or {})
        self._tasks: dict[str, asyncio.Task[None]] = {}

    async def initialize(self) -> None:
        await self.store.initialize()

    async def shutdown(self) -> None:
        tasks = tuple(task for task in self._tasks.values() if not task.done())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def list_experiments(self) -> tuple[ExperimentDefinition, ...]:
        return self.catalog.manifest.experiments

    async def create_run(
        self,
        *,
        experiment_id: str,
        tier: EvalTier,
        phase: EvalPhase,
        seeds: Sequence[int] | None = None,
    ) -> EvalRun:
        experiment = self._experiment(experiment_id, tier)
        selected_seeds = tuple(seeds) if seeds is not None else self.DEFAULT_SEEDS
        if len(selected_seeds) != 3:
            raise EvalServiceError("eval runs require exactly three recorded seeds")
        run = await self.store.create_run(
            experiment_id=experiment_id,
            tier=tier,
            phase=phase,
            seeds=selected_seeds,
        )
        if experiment is None:
            await self.store.create_arm(run.id, tier.value, settings={})
        else:
            for arm in experiment.arms:
                settings = cast(
                    Mapping[str, JsonValue],
                    arm.model_dump(mode="json", exclude={"id"}),
                )
                await self.store.create_arm(run.id, arm.id, settings=settings)
        return run

    async def start(self, run_id: str) -> EvalRun:
        run = await self.store.get_run(run_id)
        if run.status is not EvalRunStatus.CREATED:
            raise EvalServiceError("only created eval runs can start")
        queued = await self.store.set_run_status(run_id, EvalRunStatus.QUEUED)
        self._tasks[run_id] = asyncio.create_task(
            self._execute(run_id), name=f"harness-eval-{run_id}"
        )
        await asyncio.sleep(0)
        return queued

    async def cancel(self, run_id: str) -> EvalRun:
        run = await self.store.get_run(run_id)
        if run.status in {
            EvalRunStatus.COMPLETED,
            EvalRunStatus.BLOCKED,
            EvalRunStatus.CANCELED,
            EvalRunStatus.FAILED,
        }:
            return run
        await self.store.set_run_status(run_id, EvalRunStatus.CANCELING)
        await self.lease.cancel(run_id)
        task = self._tasks.get(run_id)
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        return await self.store.set_run_status(run_id, EvalRunStatus.CANCELED)

    async def wait(self, run_id: str) -> EvalRun:
        task = self._tasks.get(run_id)
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
        return await self.store.get_run(run_id)

    async def status(self, run_id: str) -> EvalRun:
        return await self.store.get_run(run_id)

    async def list_runs(self) -> list[EvalRun]:
        return await self.store.list_runs()

    async def report(self, run_id: str) -> EvalReport:
        return await self.store.get_report(run_id)

    async def list_reports(self) -> list[EvalReport]:
        return await self.store.list_reports()

    async def create_regression_draft(self, feedback: Feedback, turn: Turn) -> RegressionDraft:
        if feedback.turn_id != turn.id or turn.terminal_outcome is None:
            raise EvalServiceError("regression drafts require feedback linked to a finished Turn")
        return await self.store.create_regression_draft(
            source_feedback_sha256=_identifier_digest(feedback.id),
            source_turn_sha256=_identifier_digest(turn.id),
            rating=feedback.rating,
            comment_present=feedback.comment is not None,
            terminal_outcome_kind=turn.terminal_outcome.kind,
            terminal_outcome_reason=turn.terminal_outcome.reason_code,
        )

    async def list_regression_drafts(self) -> list[RegressionDraft]:
        return await self.store.list_regression_drafts()

    async def _execute(self, run_id: str) -> None:
        run = await self.store.get_run(run_id)
        runner = self._runners.get(run.tier)
        if runner is None:
            await self._block(run, "runner_not_configured")
            return
        acquired = False
        try:
            await self.lease.acquire(run_id)
            acquired = True
            await self.store.set_run_status(run_id, EvalRunStatus.RUNNING)
            fixtures = self._fixtures(run, runner)
            if not fixtures:
                await self._block(run, "no_deterministic_cases")
                return
            count = 15 if run.phase is EvalPhase.PILOT else 50
            arms = await self.store.list_arms(run.id)
            for arm in arms:
                schedule = _schedule(
                    fixtures,
                    count=count,
                    seeds=run.seeds,
                    cover_every_fixture=run.tier is EvalTier.MODEL_SMOKE,
                    passes=1 if run.phase is EvalPhase.PILOT else 3,
                )
                for order_index, (fixture, seed) in enumerate(schedule):
                    result = await runner.run_case(
                        EvalCaseSpec(
                            run_id=run.id,
                            arm_id=arm.arm_id,
                            fixture=fixture,
                            seed=seed,
                            order_index=order_index,
                            tier=run.tier,
                            settings=arm.settings,
                        )
                    )
                    evaluation = result.evaluation or evaluate_oracle(
                        fixture.oracle.typed_assertions,
                        result.evidence,
                    )
                    recorded = await self.store.record_case(
                        run_id=run.id,
                        arm_id=arm.id,
                        fixture_id=fixture.id,
                        seed=seed,
                        order_index=order_index,
                        verdict=evaluation.verdict,
                        terminal_outcome_kind=evaluation.terminal_outcome_kind,
                        terminal_outcome_reason=evaluation.terminal_outcome_reason,
                        security_violations=result.security_violations,
                    )
                    for name, value in result.metrics.items():
                        await self.store.record_metric(
                            run_id=run.id,
                            arm_id=arm.id,
                            case_id=recorded.id,
                            name=name,
                            value=value,
                        )
            completed = await self.store.set_run_status(run.id, EvalRunStatus.COMPLETED)
            await self._save_report(completed)
        except BenchmarkLeaseCanceled:
            await self.store.set_run_status(run.id, EvalRunStatus.CANCELED)
        except asyncio.CancelledError:
            await self.store.set_run_status(run.id, EvalRunStatus.CANCELED)
            raise
        except Exception:
            failed = await self.store.set_run_status(
                run.id, EvalRunStatus.FAILED, reason_code="runner_failed"
            )
            await self._save_report(failed)
        finally:
            if acquired and self.lease.owner == run_id:
                await self.lease.release(run_id)

    async def _block(self, run: EvalRun, reason_code: str) -> None:
        blocked = await self.store.set_run_status(
            run.id, EvalRunStatus.BLOCKED, reason_code=reason_code
        )
        await self._save_report(blocked)

    def _experiment(self, experiment_id: str, tier: EvalTier) -> ExperimentDefinition | None:
        """Returns None for a run that covers the corpus instead of comparing arms.

        A smoke run answers "does anything the harness can execute still work",
        so scoping it by an experiment's tags would leave whatever no experiment
        happens to mention unexecuted — which is how four fixtures became
        unreachable while looking like part of the corpus.
        """
        if tier is EvalTier.CONTRACT:
            if experiment_id not in {
                "contract_regressions",
                self.catalog.dataset.dataset_id,
            }:
                raise EvalServiceError(f"unknown contract experiment: {experiment_id}")
            return None
        if tier is EvalTier.MODEL_SMOKE and experiment_id in {
            EvalTier.MODEL_SMOKE.value,
            self.catalog.dataset.dataset_id,
        }:
            return None
        experiment = next(
            (item for item in self.catalog.manifest.experiments if item.id == experiment_id),
            None,
        )
        if experiment is None:
            raise EvalServiceError(f"unknown experiment: {experiment_id}")
        return experiment

    def _fixtures(self, run: EvalRun, runner: CaseRunner) -> tuple[RegressionFixture, ...]:
        fixtures = self.catalog.dataset.fixtures
        experiment = self._experiment(run.experiment_id, run.tier)
        if experiment is None:
            return tuple(item for item in fixtures if runner.supports(item.type))
        tags = frozenset(experiment.fixture_tags)
        return tuple(
            item for item in fixtures if tags.intersection(item.tags) and runner.supports(item.type)
        )

    async def _save_report(self, run: EvalRun) -> EvalReport:
        arms = await self.store.list_arms(run.id)
        cases = await self.store.list_cases(run.id)
        summaries: list[JsonValue] = []
        cases_by_arm: dict[str, list[TaskVerdict]] = {}
        security_violations = 0
        for arm in arms:
            arm_cases = [item for item in cases if item.arm_id == arm.id]
            verdicts = [item.verdict for item in arm_cases]
            cases_by_arm[arm.id] = verdicts
            security_violations += sum(item.security_violations for item in arm_cases)
            passed = sum(verdict is TaskVerdict.PASS for verdict in verdicts)
            interval = wilson_interval(passed, len(verdicts))
            summaries.append(
                {
                    "arm_id": arm.arm_id,
                    "case_count": len(verdicts),
                    "verdict_counts": {
                        verdict.value: verdicts.count(verdict) for verdict in TaskVerdict
                    },
                    "wilson_95": {
                        "estimate": interval.estimate,
                        "lower": interval.lower,
                        "upper": interval.upper,
                    },
                    "fixtures": _fixture_rates(arm_cases),
                }
            )
        gate_payload: JsonValue = None
        if run.phase is EvalPhase.PROMOTION and len(arms) >= 2:
            control = cases_by_arm[arms[0].id]
            gate_results: list[JsonValue] = []
            for candidate_arm in arms[1:]:
                gate = evaluate_promotion_gate(
                    control=control,
                    candidate=cases_by_arm[candidate_arm.id],
                    security_violations=security_violations,
                    seed=run.seeds[0],
                    max_inconclusive_pair_rate=(
                        self.catalog.manifest.promotion_protocol.max_inconclusive_pair_rate
                    ),
                )
                gate_results.append(
                    {
                        "arm_id": candidate_arm.arm_id,
                        "promoted": gate.promoted,
                        "reason_code": gate.reason_code,
                        "non_inferiority_margin": gate.non_inferiority_margin,
                        "inconclusive_pairs": gate.inconclusive_pairs,
                        "inconclusive_pair_rate": gate.inconclusive_pair_rate,
                    }
                )
            gate_payload = gate_results
        return await self.store.save_report(
            run.id,
            {
                "run_id": run.id,
                "experiment_id": run.experiment_id,
                "tier": run.tier.value,
                "phase": run.phase.value,
                "status": run.status.value,
                "reason_code": run.reason_code,
                "seeds": list(run.seeds),
                "security_violations": security_violations,
                "arm_summaries": summaries,
                "promotion_gates": gate_payload,
            },
        )


def _fixture_rates(cases: Sequence[EvalCase]) -> JsonValue:
    """Pass rate per fixture, so an unstable one reads as unstable.

    An arm-level number hides which fixture moved. With a runtime that does not
    reproduce exactly from its seed, a fixture that passes two of three readings
    is a different fact from one that fails all three, and only this breaks them
    apart.
    """
    by_fixture: dict[str, list[TaskVerdict]] = {}
    for case in cases:
        by_fixture.setdefault(case.fixture_id, []).append(case.verdict)
    rates: dict[str, JsonValue] = {}
    for fixture_id, verdicts in sorted(by_fixture.items()):
        passed = sum(item is TaskVerdict.PASS for item in verdicts)
        interval = wilson_interval(passed, len(verdicts))
        rates[fixture_id] = {
            "case_count": len(verdicts),
            "verdict_counts": {
                verdict.value: verdicts.count(verdict)
                for verdict in TaskVerdict
                if verdicts.count(verdict)
            },
            "wilson_95": {
                "estimate": interval.estimate,
                "lower": interval.lower,
                "upper": interval.upper,
            },
        }
    return rates


def _schedule(
    fixtures: Sequence[RegressionFixture],
    *,
    count: int,
    seeds: tuple[int, ...],
    cover_every_fixture: bool = False,
    passes: int = 1,
) -> tuple[tuple[RegressionFixture, int], ...]:
    """Draws `count` cases per arm, or the whole corpus once per recorded order.

    The fixed counts belong to the comparison protocol: they size an arm so two
    arms can be compared. A smoke run compares nothing, and 15 draws over a
    larger corpus leaves a seed-dependent slice of it unexecuted, so it takes
    every fixture in each of the three recorded orders instead.
    """
    orders: dict[int, list[RegressionFixture]] = {}
    positions: dict[int, int] = {}
    for seed in seeds:
        order = list(fixtures)
        random.Random(seed).shuffle(order)
        orders[seed] = order
        positions[seed] = 0
    if cover_every_fixture:
        # The local runtime is not perfectly reproducible from its seed, so one
        # reading per fixture cannot separate a broken fixture from a bad draw.
        # A promotion smoke repeats every order, and the report carries the rate.
        return tuple(
            (fixture, seed) for _ in range(passes) for seed in seeds for fixture in orders[seed]
        )
    schedule: list[tuple[RegressionFixture, int]] = []
    for index in range(count):
        seed = seeds[index % len(seeds)]
        order = orders[seed]
        position = positions[seed]
        schedule.append((order[position % len(order)], seed))
        positions[seed] = position + 1
    return tuple(schedule)


def _identifier_digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
